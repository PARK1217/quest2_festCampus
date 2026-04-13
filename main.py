import os
import shutil
import time
import json
import re
from typing import Optional
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from sqlalchemy import func

# Monitoring & Tracing
from prometheus_fastapi_instrumentator import Instrumentator
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource

# Auth & DB
from authlib.integrations.starlette_client import OAuth
from backend.database import init_db, get_db
from backend.models import (
    User, UserRole, Document, RagQuery, DocumentChunk,
    QuizAttempt, QuizQuestion, RagEvaluation, StudySession
)
from backend.auth import get_current_user, require_admin, require_user

# LangChain
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

load_dotenv()
OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY       = os.getenv("GOOGLE_API_KEY")
GROQ_API_KEY         = os.getenv("GROQ_API_KEY")
HUGGINGFACE_API_KEY  = os.getenv("HUGGINGFACE_API_KEY")

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "your-id")
GOOGLE_CLIENT_SECRET   = os.getenv("GOOGLE_CLIENT_SECRET", "your-secret")
SECRET_KEY           = os.getenv("SECRET_KEY", "any-secret-key")

init_db()

# ─── Monitoring Setup ────────────────────────
resource = Resource.create({"service.name": "rag-backend"})
provider = TracerProvider(resource=resource)
# Tempo (OTLP gRPC)로 트레이스 전송
otlp_exporter = OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)
provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# ─── App Setup ───────────────────────────────
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Prometheus 메트릭 노출 (/metrics)
instrumentator = Instrumentator().instrument(app)
instrumentator.expose(app)

# 모델별 지연 시간 추적을 위한 커스텀 메트릭 (옵션)
from prometheus_client import Histogram
MODEL_LATENCY = Histogram(
    "rag_model_latency_seconds",
    "RAG model response latency in seconds",
    ["provider"]
)
# OpenTelemetry 자동 계측
FastAPIInstrumentor.instrument_app(app)

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ─── RAG Engine ──────────────────────────────
class RAGEngine:
    def __init__(self):
        # 모든 모델이 공용으로 사용할 고성능 다국어 임베딩 모델 (로컬 CPU)
        # 한국어 지원이 잘 되는 모델을 선택합니다.
        self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        self.vector_store = None

    def _get_llm(self, provider: str):
        if provider == "gemini" and GOOGLE_API_KEY:
            # 2.0 모델의 할당량 문제(Limit 0)를 피하기 위해 안정적인 1.5 Flash 모델 사용
            # 이전에 안됐던 현상은 safety_settings 미적용 때문일 가능성이 큼
            return ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                google_api_key=GOOGLE_API_KEY,
                temperature=0,
                safety_settings={
                    "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
                    "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
                    "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
                }
            )

        if provider == "groq" and GROQ_API_KEY:
            return ChatGroq(model_name="llama-3.1-8b-instant", groq_api_key=GROQ_API_KEY)
        if provider == "huggingface" and HUGGINGFACE_API_KEY:
            if GROQ_API_KEY:
                return ChatGroq(model_name="llama-3.1-8b-instant", groq_api_key=GROQ_API_KEY)
        
        # Default fallback to OpenAI if available
        if OPENAI_API_KEY:
            return ChatOpenAI(model="gpt-4o-mini", temperature=0, openai_api_key=OPENAI_API_KEY)
        return None

    def process_document(self, file_path: str):
        loader   = PyPDFLoader(file_path) if file_path.endswith(".pdf") else TextLoader(file_path)
        docs     = loader.load()
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        splits   = splitter.split_documents(docs)
        
        if self.vector_store is None:
            self.vector_store = FAISS.from_documents(splits, self.embeddings)
        else:
            self.vector_store.add_documents(splits)

    def reset_and_rebuild_index(self, db: Session):
        """데이터베이스의 모든 문서를 기반으로 벡터 인덱스를 재구축합니다."""
        self.vector_store = None
        docs = db.query(Document).all()
        for d in docs:
            if os.path.exists(d.file_path):
                self.process_document(d.file_path)

    def ask_chatbot(self, query: str, provider: str = "openai", db: Session = None) -> dict:
        if not self.vector_store and db:
            # 인덱스가 비어있는데 DB에 문서가 있다면 자동 재구축 시도
            doc_count = db.query(Document).count()
            if doc_count > 0:
                self.reset_and_rebuild_index(db)

        if not self.vector_store:
            return {"answer": "학습된 데이터가 없습니다. 먼저 파일을 업로드해주세요.", "sources": []}
        
        llm = self._get_llm(provider)
        if not llm:
            return {"answer": f"{provider} API 키가 설정되지 않았습니다.", "sources": []}
            
        # 관련 문서 검색
        docs = self.vector_store.as_retriever(search_kwargs={"k": 3}).invoke(query)
        # 출처 파일명 추출
        sources = list(set([os.path.basename(d.metadata.get("source", "알 수 없음")) for d in docs]))
        
        context = "\n".join([d.page_content for d in docs])
        prompt  = f"다음 컨텍스트를 바탕으로 질문에 한국어로 답해주세요.\n\n컨텍스트:\n{context}\n\n질문: {query}"
        
        try:
            answer = llm.invoke(prompt).content
            return {"answer": answer, "sources": sources}
        except Exception as e:
            return f"[{provider}] API 호출 실패: {str(e)}"

    def generate_questions(self, provider: str = "openai", db: Session = None) -> str:
        if not self.vector_store and db:
            doc_count = db.query(Document).count()
            if doc_count > 0:
                self.reset_and_rebuild_index(db)

        if not self.vector_store:
            return "[]"
        
        llm = self._get_llm(provider)
        if not llm: return "[]"
        
        retriever = self.vector_store.as_retriever(search_kwargs={"k": 5})
        docs      = retriever.invoke("주요 개념과 핵심 내용")
        context   = "\n".join([d.page_content for d in docs])
        prompt    = (
            "다음 컨텍스트를 바탕으로 학습용 객관식 문제 5개를 생성해주세요.\n"
            "반드시 오직 JSON 배열 형식으로만 출력하세요. 다른 말(인사말 등)이나 코드 블록 기호(```json)는 절대 사용하지 마세요.\n"
            "각 항목은 'question'(문자열), 'options'(4개 선택지 문자열 배열), 'answer'(정답 선택지 문자열) 키를 가져야 합니다.\n\n"
            f"컨텍스트:\n{context}"
        )
        return llm.invoke(prompt).content


rag_engine = RAGEngine()


# ─── Request Models ──────────────────────────
class GenerateQuestionsRequest(BaseModel):
    document_id: int

class QuizAttemptRequest(BaseModel):
    question_id: int
    user_answer: str


# ─── Auth Routes ─────────────────────────────
@app.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("auth")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth")
async def auth(request: Request, db: Session = Depends(get_db)):
    token     = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")
    if not user_info:
        raise HTTPException(status_code=400, detail="Google 인증 실패")

    email = user_info["email"]
    user  = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, name=user_info["name"], role=UserRole.user)
        db.add(user)
        db.commit()
        db.refresh(user)

    request.session["user"] = {"email": user.email, "name": user.name, "role": user.role}
    return RedirectResponse(url="/")


@app.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/")


# ─── User API (일반 사용자 접근 가능) ────────
@app.get("/api/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return {"email": current_user.email, "name": current_user.name, "role": current_user.role}


@app.get("/api/available-models")
async def get_available_models():
    models = []
    if OPENAI_API_KEY:
        models.append({"id": "openai", "name": "OpenAI (GPT-4o-mini)"})
    if GOOGLE_API_KEY:
        models.append({"id": "gemini", "name": "Google Gemini (1.5 Flash - 안정 버전)"})
    if GROQ_API_KEY:

        models.append({"id": "groq", "name": "Groq (Llama 3.1 - 초고속)"})
    if HUGGINGFACE_API_KEY:
        models.append({"id": "huggingface", "name": "HuggingFace (무료)"})
    
    # 만약 아무 키도 없다면 로컬 HF 임베딩이라도 쓸 수 있게 'free' 유지 (LLM은 fallback 필요)
    if not models:
        models.append({"id": "free", "name": "로컬 CPU 모델 (기본)"})
    return models


@app.get("/api/stats")
async def get_stats(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    total_docs     = db.query(Document).filter(Document.user_id == current_user.id).count()
    total_queries  = db.query(RagQuery).filter(RagQuery.user_id == current_user.id).count()
    total_attempts = db.query(QuizAttempt).filter(QuizAttempt.user_id == current_user.id).count()
    correct_count  = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.is_correct == True,
    ).count()
    correct_rate = round(correct_count / total_attempts * 100, 1) if total_attempts else 0
    return {
        "total_docs":    total_docs,
        "total_queries": total_queries,
        "total_attempts": total_attempts,
        "correct_count": correct_count,
        "correct_rate":  correct_rate,
        "progress":      correct_rate,
    }


@app.get("/api/documents")
async def get_documents(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    docs = (
        db.query(Document)
        .filter(Document.user_id == current_user.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )
    return [
        {
            "id":          d.id,
            "filename":    d.filename,
            "file_type":   d.file_type,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
        }
        for d in docs
    ]


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    os.makedirs("uploads", exist_ok=True)
    path = f"uploads/{file.filename}"
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = Document(
        user_id   = current_user.id,
        filename  = file.filename,
        file_path = path,
        file_type = file.filename.rsplit(".", 1)[-1],
    )
    db.add(doc)
    db.commit()

    rag_engine.process_document(path)
    return {"message": "업로드 성공 (통합 벡터 DB 구축)", "document_id": doc.id}


@app.delete("/api/documents/{document_id}")
async def delete_document(
    document_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == current_user.id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없거나 권한이 없습니다.")

    # 물리 파일 삭제
    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    # DB 삭제 (cascade 설정으로 하위 항목도 자동 삭제됨)
    db.delete(doc)
    db.commit()

    # 벡터 인덱스 재구축
    rag_engine.reset_and_rebuild_index(db)
    return {"message": "문서 삭제 성공"}


@app.get("/api/chat")
async def chat(
    query: str,
    document_id: int,
    provider: str = "openai",
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not query.strip():
        raise HTTPException(status_code=400, detail="질문 내용을 입력해주세요.")

    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == current_user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    try:
        start  = time.time()
        result = rag_engine.ask_chatbot(query, provider=provider, db=db)

        # 에러 문자열 반환 처리 (observe 전에 체크)
        if isinstance(result, str):
            raise Exception(result)

        # 성공한 경우에만 지연 시간 기록
        latency_s = time.time() - start
        MODEL_LATENCY.labels(provider=provider).observe(latency_s)
        latency   = int(latency_s * 1000)

        rag_q = RagQuery(
            user_id     = current_user.id,
            document_id = document_id,
            query       = query,
            response    = result["answer"],
            model_used  = provider,
            latency_ms  = latency,
        )
        db.add(rag_q)
        db.commit()

        return {
            "response": result["answer"],
            "model": provider,
            "sources": result["sources"]
        }
    except Exception as e:
        error_detail = str(e)
        print(f"ERROR [Chat/{provider}]: {error_detail}")
        raise HTTPException(status_code=500, detail=f"[{provider}] 모델 응답 실패: {error_detail}")


@app.get("/api/chat/history")
async def get_chat_history(
    document_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    # 인덱스가 비어있는 경우 history 요청 시에도 체크
    if not rag_engine.vector_store:
        doc_count = db.query(Document).count()
        if doc_count > 0:
            rag_engine.reset_and_rebuild_index(db)

    history = (
        db.query(RagQuery)
        .filter(
            RagQuery.user_id == current_user.id,
            RagQuery.document_id == document_id
        )
        .order_by(RagQuery.queried_at.asc())
        .all()
    )
    
    messages = []
    for q in history:
        if q.query.strip():
            messages.append({"role": "user", "content": q.query})
        if q.response and q.response.strip():
            messages.append({
                "role": "assistant", 
                "content": q.response,
                "model": q.model_used
            })
            
    return messages


@app.post("/api/questions/generate")
async def generate_questions(
    req: GenerateQuestionsRequest,
    provider: str = "openai",
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(
        Document.id == req.document_id,
        Document.user_id == current_user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    try:
        start_time = time.time()
        raw = rag_engine.generate_questions(provider=provider, db=db)
        # 지연 시간 기록
        MODEL_LATENCY.labels(provider=provider).observe(time.time() - start_time)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 모델 응답 오류: {str(e)}")
    
    # 정규표현식으로 JSON 배열 부분([ ... ])만 추출
    raw = raw.strip()
    match = re.search(r"(\[.*\])", raw, re.DOTALL)
    if match:
        raw = match.group(1)
    
    # 마크다운 코드 블록 제거
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        questions_data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            # json.loads 실패 시 ast.literal_eval로 재시도 (더 유연한 파싱)
            import ast
            questions_data = ast.literal_eval(raw)
        except:
            print(f"DEBUG: JSON Parsing Failed. Raw output: {raw[:300]}...")
            raise HTTPException(status_code=500, detail="문제 생성 실패: AI가 유효하지 않은 데이터 형식을 반환했습니다.")

    if not isinstance(questions_data, list):
        raise HTTPException(status_code=500, detail="문제 생성 실패: 결과가 리스트 형식이 아닙니다.")

    saved = []
    for q in questions_data:
        # 데이터 구조 보정
        question_text = q.get("question", "")
        choices = q.get("options") or q.get("choices") or []
        answer = q.get("answer", "")
        
        if not question_text or not choices: continue

        qq = QuizQuestion(
            document_id    = req.document_id,
            question       = question_text,
            choices        = choices,
            correct_answer = str(answer),
        )
        db.add(qq)
        db.flush()
        saved.append(qq)
    db.commit()

    return [
        {"id": qq.id, "question": qq.question, "choices": qq.choices}
        for qq in saved
    ]


@app.get("/api/questions")
async def get_questions(
    document_id: Optional[int] = None,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if document_id is not None:
        doc = db.query(Document).filter(
            Document.id == document_id,
            Document.user_id == current_user.id,
        ).first()
        if not doc:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
        questions = db.query(QuizQuestion).filter(QuizQuestion.document_id == document_id).all()
    else:
        questions = (
            db.query(QuizQuestion)
            .join(Document)
            .filter(Document.user_id == current_user.id)
            .all()
        )
    
    # 해당 사용자가 맞힌 문제 ID 목록
    solved_attempts = db.query(QuizAttempt.question_id).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.is_correct == True
    ).all()
    solved_ids = {a[0] for a in solved_attempts}

    # 해당 사용자가 시도한 모든 문제 ID
    attempted_attempts = db.query(QuizAttempt.question_id).filter(
        QuizAttempt.user_id == current_user.id
    ).all()
    attempted_ids = {a[0] for a in attempted_attempts}

    return [
        {
            "id":          qq.id,
            "document_id": qq.document_id,
            "question":    qq.question,
            "choices":     qq.choices,
            "status":      "solved" if qq.id in solved_ids else ("wrong" if qq.id in attempted_ids else "new")
        }
        for qq in questions
    ]


@app.delete("/api/questions")
async def delete_questions(
    document_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    # 권한 확인
    doc = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == current_user.id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없거나 권한이 없습니다.")

    # 해당 문서의 모든 문제 삭제 (cascade 설정에 의해 시도 내역도 삭제됨)
    db.query(QuizQuestion).filter(QuizQuestion.document_id == document_id).delete()
    db.commit()
    return {"message": "문제가 성공적으로 삭제되었습니다."}


@app.post("/api/questions/attempt")
async def submit_attempt(
    req: QuizAttemptRequest,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    qq = db.query(QuizQuestion).filter(QuizQuestion.id == req.question_id).first()
    if not qq:
        raise HTTPException(status_code=404, detail="문제를 찾을 수 없습니다.")

    is_correct = req.user_answer.strip() == qq.correct_answer.strip()
    attempt = QuizAttempt(
        user_id     = current_user.id,
        question_id = req.question_id,
        user_answer = req.user_answer,
        is_correct  = is_correct,
    )
    db.add(attempt)
    db.commit()

    return {
        "question_id":    req.question_id,
        "is_correct":     is_correct,
        "correct_answer": qq.correct_answer,
    }


# ─── Admin API (관리자 전용) ──────────────────
@app.get("/api/admin/stats")
async def admin_stats(
    admin: User = Depends(require_admin),
    db: Session  = Depends(get_db),
):
    """전체 사용량 통계 — admin 전용"""
    total_users    = db.query(User).count()
    total_docs     = db.query(Document).count()
    total_queries  = db.query(RagQuery).count()
    return {
        "total_users":   total_users,
        "total_docs":    total_docs,
        "total_queries": total_queries,
    }


@app.get("/api/admin/users")
async def admin_users(
    admin: User = Depends(require_admin),
    db: Session  = Depends(get_db),
):
    """전체 사용자 목록 + 권한 — admin 전용"""
    users = db.query(User).all()
    return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role} for u in users]


@app.get("/api/admin/overview")
async def admin_overview(
    admin: User = Depends(require_admin),
    db: Session  = Depends(get_db),
):
    """관리자 대시보드 종합 통계"""
    # ── 기본 카운트 ──
    total_users         = db.query(User).count()
    total_docs          = db.query(Document).count()
    total_queries       = db.query(RagQuery).count()
    total_chunks        = db.query(DocumentChunk).count()
    total_quiz_attempts = db.query(QuizAttempt).count()
    correct_attempts    = db.query(QuizAttempt).filter(QuizAttempt.is_correct == True).count()

    # ── RAG 평가 평균 ──
    eval_avg = db.query(
        func.avg(RagEvaluation.faithfulness),
        func.avg(RagEvaluation.answer_relevancy),
        func.avg(RagEvaluation.context_precision),
        func.avg(RagEvaluation.context_recall),
    ).first()

    def pct(val):
        return round((val or 0) * 100, 1)

    # ── 최근 쿼리 5건 ──
    recent_queries = (
        db.query(RagQuery)
        .order_by(RagQuery.queried_at.desc())
        .limit(5)
        .all()
    )

    # ── 문서별 쿼리 수 ──
    doc_stats = (
        db.query(Document.filename, func.count(RagQuery.id).label("cnt"))
        .outerjoin(RagQuery, Document.id == RagQuery.document_id)
        .group_by(Document.id, Document.filename)
        .all()
    )

    # ── 사용자별 쿼리 수 ──
    user_stats = (
        db.query(User.name, User.email, func.count(RagQuery.id).label("cnt"))
        .outerjoin(RagQuery, User.id == RagQuery.user_id)
        .group_by(User.id, User.name, User.email)
        .order_by(func.count(RagQuery.id).desc())
        .limit(10)
        .all()
    )

    # ── 모델별 성능 통계 ──
    model_performance = (
        db.query(
            RagQuery.model_used,
            func.count(RagQuery.id).label("total_calls"),
            func.avg(RagQuery.latency_ms).label("avg_latency")
        )
        .group_by(RagQuery.model_used)
        .all()
    )

    return {
        "summary": {
            "total_users":   total_users,
            "total_docs":    total_docs,
            "total_queries": total_queries,
            "total_chunks":  total_chunks,
            "quiz_accuracy": round(correct_attempts / total_quiz_attempts * 100, 1) if total_quiz_attempts else 0,
        },
        "model_stats": [
            {
                "model": m.model_used,
                "count": m.total_calls,
                "avg_latency": round(m.avg_latency, 1) if m.avg_latency else 0
            } for m in model_performance
        ],
        "rag_evaluation": {
            "faithfulness":       pct(eval_avg[0]),
            "answer_relevancy":   pct(eval_avg[1]),
            "context_precision":  pct(eval_avg[2]),
            "context_recall":     pct(eval_avg[3]),
        },
        "recent_queries": [
            {
                "query":      q.query[:60] + ("…" if len(q.query) > 60 else ""),
                "model":      q.model_used,
                "latency_ms": q.latency_ms,
                "queried_at": q.queried_at.strftime("%Y-%m-%d %H:%M") if q.queried_at else "-",
            }
            for q in recent_queries
        ],
        "doc_stats":  [{"filename": d.filename, "query_count": d.cnt}  for d in doc_stats],
        "user_stats": [{"name": u.name, "email": u.email, "query_count": u.cnt} for u in user_stats],
    }


@app.put("/api/admin/users/{user_id}/role")
async def update_user_role(
    user_id: int,
    role: UserRole,
    admin: User  = Depends(require_admin),
    db: Session  = Depends(get_db),
):
    """사용자 권한 변경 — admin 전용"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    target.role = role
    db.commit()
    return {"message": f"{target.email} 권한이 {role}으로 변경됐습니다."}


# ─── Static Files ────────────────────────────
@app.get("/")
async def serve_index():
    return FileResponse("frontend/index.html")


@app.get("/{path:path}")
async def serve_static(path: str):
    file_path = os.path.join("frontend", path)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return FileResponse("frontend/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
