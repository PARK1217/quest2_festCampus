import os
import shutil
import time
import json
import re
import logging
from typing import List, Optional
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
    QuizAttempt, QuizQuestion, RagEvaluation, StudySession, get_kst_now
)
from backend.auth import get_current_user, require_admin, require_user

# LangChain
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

load_dotenv()
OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY       = os.getenv("GOOGLE_API_KEY")
GROQ_API_KEY         = os.getenv("GROQ_API_KEY")
HUGGINGFACE_API_KEY  = os.getenv("HUGGINGFACE_API_KEY")
MISTRAL_API_KEY      = os.getenv("MISTRAL_API_KEY")
CEREBRAS_API_KEY     = os.getenv("CEREBRAS_API_KEY")
COHERE_API_KEY       = os.getenv("COHERE_API_KEY")
OLLAMA_BASE_URL      = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL         = os.getenv("OLLAMA_MODEL", "llama3.2")

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "your-id")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "your-secret")
SECRET_KEY           = os.getenv("SECRET_KEY", "any-secret-key")

init_db()

# ─── Monitoring Setup ────────────────────────
LOKI_URL      = os.getenv("LOKI_URL",                        "http://localhost:3100")
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT",    "http://localhost:4317")

# ── 로깅 기본 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rag-backend")

# ── Loki 핸들러 (미실행 시 무시하고 계속 동작)
try:
    import logging_loki
    loki_handler = logging_loki.LokiHandler(
        url=f"{LOKI_URL}/loki/api/v1/push",
        tags={"service": "rag-backend"},
        version="1",
    )
    loki_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(loki_handler)
    # uvicorn 접근 로그도 Loki로
    logging.getLogger("uvicorn.access").addHandler(loki_handler)
    logging.getLogger("uvicorn.error").addHandler(loki_handler)
    logger.info("Loki 핸들러 연결 완료", extra={"tags": {"event": "startup"}})
except Exception as _loki_err:
    print(f"[Loki] 연결 실패 — Loki 미실행 시 무시됩니다: {_loki_err}")

# ── Tempo (OTLP gRPC) 트레이스
resource = Resource.create({"service.name": "rag-backend"})
provider = TracerProvider(resource=resource)
try:
    otlp_exporter = OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    logger.info(f"Tempo OTLP 연결 설정: {OTLP_ENDPOINT}")
except Exception as _tempo_err:
    print(f"[Tempo] OTLP 설정 실패: {_tempo_err}")
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# ─── App Setup ───────────────────────────────
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Prometheus 메트릭 노출 (/metrics)
instrumentator = Instrumentator().instrument(app)
instrumentator.expose(app)

# Tempo — 모든 FastAPI 요청에 자동으로 trace span 생성
FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

# 모델별 지연 시간 추적을 위한 커스텀 메트릭 (옵션)
from prometheus_client import Histogram
MODEL_LATENCY = Histogram(
    "rag_model_latency_seconds",
    "RAG model response latency in seconds",
    ["provider"]
)

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ─── 모델 API 에러 ────────────────────────────
class ModelAPIError(Exception):
    """LLM API 호출 중 발생하는 분류된 에러"""
    def __init__(self, error_code: str, message: str):
        self.error_code = error_code   # rate_limit | not_found | bad_request | error
        self.message    = message
        super().__init__(message)


# ─── RAG Engine ──────────────────────────────
class RAGEngine:
    def __init__(self):
        self.embeddings   = HuggingFaceEmbeddings(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        self.vector_store = None

    def _get_llm(self, provider: str):
        # ── 기존 모델 ──────────────────────────
        if provider == "openai" and OPENAI_API_KEY:
            return ChatOpenAI(model="gpt-4o-mini", temperature=0, openai_api_key=OPENAI_API_KEY)

        if provider == "gemini" and GOOGLE_API_KEY:
            return ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                google_api_key=GOOGLE_API_KEY,
                temperature=0,
                safety_settings={
                    "HARM_CATEGORY_HARASSMENT":       "BLOCK_NONE",
                    "HARM_CATEGORY_HATE_SPEECH":       "BLOCK_NONE",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
                    "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
                }
            )

        if provider == "groq" and GROQ_API_KEY:
            return ChatGroq(model_name="llama-3.1-8b-instant", groq_api_key=GROQ_API_KEY)

        if provider == "huggingface" and HUGGINGFACE_API_KEY and GROQ_API_KEY:
            return ChatGroq(model_name="llama-3.1-8b-instant", groq_api_key=GROQ_API_KEY)

        # ── Groq 추가 모델 (기존 키 재사용) ────
        if provider == "groq-70b" and GROQ_API_KEY:
            return ChatGroq(model_name="llama-3.3-70b-versatile", groq_api_key=GROQ_API_KEY)

        if provider == "groq-mixtral" and GROQ_API_KEY:
            return ChatGroq(model_name="moonshotai/kimi-k2-instruct", groq_api_key=GROQ_API_KEY)

        if provider == "groq-gemma" and GROQ_API_KEY:
            return ChatGroq(model_name="llama-3.1-8b-instant", groq_api_key=GROQ_API_KEY)

        # ── 신규 무료 외부 모델 ─────────────────
        if provider == "mistral" and MISTRAL_API_KEY:
            try:
                from langchain_mistralai import ChatMistralAI
                return ChatMistralAI(model="mistral-small-latest", api_key=MISTRAL_API_KEY, temperature=0)
            except ImportError:
                pass

        if provider == "cerebras" and CEREBRAS_API_KEY:
            try:
                from langchain_cerebras import ChatCerebras
                # Correct Cerebras model name
                return ChatCerebras(model="llama-3.1-70b", api_key=CEREBRAS_API_KEY)
            except ImportError:
                pass

        if provider == "cohere" and COHERE_API_KEY:
            try:
                from langchain_cohere import ChatCohere
                # Correct Cohere model name
                return ChatCohere(model="command-r-v01", cohere_api_key=COHERE_API_KEY, temperature=0)
            except ImportError:
                pass

        # ── Ollama (로컬, API 키 없음) ──────────
        if provider == "ollama":
            try:
                from langchain_ollama import ChatOllama
                return ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
            except ImportError:
                pass

        # ── Fallback ────────────────────────────
        if OPENAI_API_KEY:
            return ChatOpenAI(model="gpt-4o-mini", temperature=0, openai_api_key=OPENAI_API_KEY)
        if GROQ_API_KEY:
            return ChatGroq(model_name="llama-3.1-8b-instant", groq_api_key=GROQ_API_KEY)
        return None

    def process_document(self, file_path: str, user_id: int, document_id: int = None, db: Session = None):
        loader   = PyPDFLoader(file_path) if file_path.endswith(".pdf") else TextLoader(file_path)
        docs     = loader.load()
        
        # 메타데이터에 소유자 정보 주입 (보안 격리용)
        for d in docs:
            d.metadata["user_id"] = user_id
            if document_id:
                d.metadata["document_id"] = document_id

        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        splits   = splitter.split_documents(docs)

        if self.vector_store is None:
            self.vector_store = FAISS.from_documents(splits, self.embeddings)
        else:
            self.vector_store.add_documents(splits)

        if document_id and db:
            for i, split in enumerate(splits):
                db.add(DocumentChunk(
                    document_id = document_id,
                    chunk_index = i,
                    content     = split.page_content,
                    token_count = len(split.page_content.split()),
                ))
            db.commit()

    def reset_and_rebuild_index(self, db: Session):
        self.vector_store = None
        docs = db.query(Document).filter(Document.is_deleted.isnot(True)).all()
        for d in docs:
            if os.path.exists(d.file_path):
                self.process_document(d.file_path, user_id=d.user_id, document_id=d.id)

    def ask_chatbot(self, query: str, user_id: int, provider: str = "openai",
                    db: Session = None, document_ids: list = None) -> dict:
        if not self.vector_store and db:
            if db.query(Document).count() > 0:
                self.reset_and_rebuild_index(db)

        if not self.vector_store:
            return {"answer": "학습된 데이터가 없습니다. 먼저 파일을 업로드해주세요.", "sources": []}

        llm = self._get_llm(provider)
        if not llm:
            return {"answer": f"{provider} API 키가 설정되지 않았습니다.", "sources": []}

        # 사용자 격리 + 선택한 문서 필터 (callable로 다중 ID 지원)
        doc_ids_set = set(document_ids) if document_ids else None
        def _filter(meta):
            if meta.get("user_id") != user_id:
                return False
            if doc_ids_set:
                return meta.get("document_id") in doc_ids_set
            return True

        retriever = self.vector_store.as_retriever(
            search_kwargs={"k": 3, "filter": _filter}
        )
        retrieved = retriever.invoke(query)
        
        sources   = list(set(os.path.basename(d.metadata.get("source", "알 수 없음")) for d in retrieved))
        context   = "\n".join(d.page_content for d in retrieved)
        prompt    = f"다음 컨텍스트를 바탕으로 질문에 한국어로 답해주세요.\n\n컨텍스트:\n{context}\n\n질문: {query}"

        try:
            response_obj  = llm.invoke(prompt)
            answer        = response_obj.content
            usage         = getattr(response_obj, "usage_metadata", None) or {}
            input_tokens  = usage.get("input_tokens")  or usage.get("prompt_tokens")
            output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
            return {"answer": answer, "sources": sources,
                    "input_tokens": input_tokens, "output_tokens": output_tokens}
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err or "quota" in err.lower() or "rate_limit" in err.lower() or "rate limit" in err.lower():
                raise ModelAPIError("rate_limit",
                    f"{provider} 모델의 무료 사용 한도를 초과했습니다.\n잠시 후 다시 시도하거나 다른 모델을 선택해주세요.")
            elif "404" in err or "NOT_FOUND" in err or ("not found" in err.lower() and "model" in err.lower()):
                raise ModelAPIError("not_found",
                    f"{provider} 모델을 찾을 수 없습니다.\n모델이 지원 종료되었거나 API 키를 확인해주세요.")
            elif "400" in err or "decommissioned" in err.lower() or "invalid_request" in err.lower():
                raise ModelAPIError("bad_request",
                    f"{provider} 모델 요청이 거부되었습니다.\n모델이 폐기되었거나 요청 형식을 확인해주세요.")
            else:
                raise ModelAPIError("error",
                    f"{provider} 모델 호출 중 오류가 발생했습니다.")

    def generate_questions(self, user_id: int, provider: str = "openai",
                           db: Session = None, document_ids: list = None) -> str:
        if not self.vector_store and db:
            if db.query(Document).count() > 0:
                self.reset_and_rebuild_index(db)

        if not self.vector_store:
            return "[]"

        llm = self._get_llm(provider)
        if not llm:
            return "[]"

        doc_ids_set = set(document_ids) if document_ids else None
        def _filter(meta):
            if meta.get("user_id") != user_id:
                return False
            if doc_ids_set:
                return meta.get("document_id") in doc_ids_set
            return True

        retriever = self.vector_store.as_retriever(
            search_kwargs={"k": 5, "filter": _filter}
        )
        docs      = retriever.invoke("주요 개념과 핵심 내용")
        context   = "\n".join(d.page_content for d in docs)
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
    document_ids: List[int]

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


# ─── Global Caches ────────────────────────────
_AVAILABLE_MODELS_CACHE = []

# ─── User API (일반 사용자 접근 가능) ────────
@app.get("/api/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return {"email": current_user.email, "name": current_user.name, "role": current_user.role}


@app.get("/api/available-models")
async def get_available_models():
    global _AVAILABLE_MODELS_CACHE
    if _AVAILABLE_MODELS_CACHE:
        return _AVAILABLE_MODELS_CACHE

    models = []

    # ── 유료 / 무료티어 외부 API ──
    if OPENAI_API_KEY:
        models.append({"id": "openai",       "name": "OpenAI · GPT-4o-mini",             "badge": "유료"})
    if GOOGLE_API_KEY:
        models.append({"id": "gemini",       "name": "Google · Gemini 2.0 Flash",        "badge": "무료티어"})
    if GROQ_API_KEY:
        models.append({"id": "groq",         "name": "Groq · Llama 3.1 8B  ⚡ 초고속",  "badge": "무료"})
        models.append({"id": "groq-70b",     "name": "Groq · Llama 3.3 70B  🧠 고품질", "badge": "무료"})
        models.append({"id": "groq-mixtral", "name": "Groq · Kimi K2  📄 긴문서",        "badge": "무료"})
        models.append({"id": "groq-gemma",   "name": "Groq · Llama 3.1 8B Instant ⚡", "badge": "무료"})
    if HUGGINGFACE_API_KEY:
        models.append({"id": "huggingface",  "name": "HuggingFace (무료)",               "badge": "무료"})
    if MISTRAL_API_KEY:
        models.append({"id": "mistral",      "name": "Mistral · Small Latest",           "badge": "무료티어"})
    if CEREBRAS_API_KEY:
        models.append({"id": "cerebras",     "name": "Cerebras · Llama 3.1 70B  ⚡",    "badge": "무료"})
    if COHERE_API_KEY:
        models.append({"id": "cohere",       "name": "Cohere · Command-R  🔍 RAG특화",  "badge": "무료티어"})

    # ── Ollama (로컬 실행 여부 확인) ──
    try:
        import httpx
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=1.0) # 타임아웃 단축
        if r.status_code == 200:
            models.append({"id": "ollama", "name": f"Ollama · {OLLAMA_MODEL}  🏠 로컬", "badge": "무료"})
    except Exception:
        pass

    if not models:
        models.append({"id": "free", "name": "로컬 CPU 모델 (기본)", "badge": "무료"})
    
    _AVAILABLE_MODELS_CACHE = models
    return models


@app.get("/api/stats")
async def get_stats(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    total_docs     = db.query(Document).filter(Document.user_id == current_user.id, Document.is_deleted.isnot(True)).count()
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


@app.get("/api/dashboard")
async def get_dashboard(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    # ── 기본 통계 ──
    total_docs     = db.query(Document).filter(Document.user_id == current_user.id, Document.is_deleted.isnot(True)).count()
    total_queries  = db.query(RagQuery).filter(RagQuery.user_id == current_user.id).count()
    total_attempts = db.query(QuizAttempt).filter(QuizAttempt.user_id == current_user.id).count()
    correct_count  = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.is_correct == True,
    ).count()
    correct_rate = round(correct_count / total_attempts * 100, 1) if total_attempts else 0

    # ── 모델별 사용 현황 ──
    model_rows = (
        db.query(RagQuery.model_used, func.count(RagQuery.id).label("cnt"))
        .filter(RagQuery.user_id == current_user.id)
        .group_by(RagQuery.model_used)
        .order_by(func.count(RagQuery.id).desc())
        .all()
    )
    total_calls = sum(r.cnt for r in model_rows)
    model_usage = [
        {
            "model": r.model_used or "알 수 없음",
            "count": r.cnt,
            "pct":   round(r.cnt / total_calls * 100) if total_calls else 0,
        }
        for r in model_rows
    ]

    # ── 최근 질문 5건 ──
    recent_rows = (
        db.query(RagQuery)
        .filter(RagQuery.user_id == current_user.id)
        .order_by(RagQuery.queried_at.desc())
        .limit(5)
        .all()
    )
    recent_queries = [
        {
            "query":      q.query,
            "response":   (q.response or "")[:150] + ("…" if len(q.response or "") > 150 else ""),
            "model":      q.model_used,
            "queried_at": q.queried_at.strftime("%Y-%m-%d %H:%M") if q.queried_at else "-",
        }
        for q in recent_rows
    ]

    # ── 틀린 문제 10건 (최근순) ──
    wrong_rows = (
        db.query(QuizAttempt, QuizQuestion)
        .join(QuizQuestion, QuizAttempt.question_id == QuizQuestion.id)
        .filter(
            QuizAttempt.user_id == current_user.id,
            QuizAttempt.is_correct == False,
        )
        .order_by(QuizAttempt.attempted_at.desc())
        .limit(10)
        .all()
    )
    wrong_answers = [
        {
            "question":       qq.question,
            "choices":        qq.choices,
            "user_answer":    a.user_answer,
            "correct_answer": qq.correct_answer,
        }
        for a, qq in wrong_rows
    ]

    return {
        "stats": {
            "total_docs":    total_docs,
            "total_queries": total_queries,
            "total_attempts": total_attempts,
            "correct_count": correct_count,
            "correct_rate":  correct_rate,
            "progress":      correct_rate,
        },
        "model_usage":    model_usage,
        "recent_queries": recent_queries,
        "wrong_answers":  wrong_answers,
    }


@app.get("/api/documents")
async def get_documents(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    docs = (
        db.query(Document)
        .filter(Document.user_id == current_user.id, Document.is_deleted.isnot(True))
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
    files: List[UploadFile] = File(...),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    os.makedirs("uploads", exist_ok=True)
    uploaded = []
    with tracer.start_as_current_span("upload_files") as span:
      span.set_attribute("user.id", current_user.id)
      span.set_attribute("files.count", len(files))
    for file in files:
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
        rag_engine.process_document(path, user_id=current_user.id, document_id=doc.id, db=db)
        uploaded.append({"document_id": doc.id, "filename": file.filename})
        logger.info(f"파일 업로드 완료: {file.filename}", extra={"tags": {"user_id": str(current_user.id), "event": "upload"}})

    return {"message": f"{len(uploaded)}개 파일 업로드 성공", "uploaded": uploaded}


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

    # 소프트 딜리트 — is_deleted 플래그 변경 + 파일을 temp 폴더로 이동
    doc.is_deleted = True
    db.commit()

    if os.path.exists(doc.file_path):
        os.makedirs("temp", exist_ok=True)
        dest = os.path.join("temp", os.path.basename(doc.file_path))
        # 같은 이름 파일이 temp에 있으면 ID 접두어로 구분
        if os.path.exists(dest):
            dest = os.path.join("temp", f"{document_id}_{os.path.basename(doc.file_path)}")
        shutil.move(doc.file_path, dest)

    # 벡터 인덱스 재구축 (삭제된 문서 제외)
    rag_engine.reset_and_rebuild_index(db)
    return {"message": "문서가 비활성화되었습니다."}


@app.get("/api/chat")
async def chat(
    query: str,
    document_ids: str,          # 쉼표 구분 문자열 예) "1,2,3"
    provider: str = "openai",
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not query.strip():
        raise HTTPException(status_code=400, detail="질문 내용을 입력해주세요.")

    # 파싱 및 소유권 검증
    try:
        id_list = [int(x) for x in document_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="document_ids 형식이 올바르지 않습니다.")
    if not id_list:
        raise HTTPException(status_code=400, detail="문서를 하나 이상 선택해주세요.")

    owned = db.query(Document).filter(
        Document.id.in_(id_list),
        Document.user_id == current_user.id,
    ).count()
    if owned != len(id_list):
        raise HTTPException(status_code=404, detail="선택한 문서 중 존재하지 않는 문서가 있습니다.")

    primary_doc_id = id_list[0]   # FK NOT NULL 제약용 대표 ID

    start = time.time()
    with tracer.start_as_current_span("rag_chat") as span:
        span.set_attribute("user.id",   current_user.id)
        span.set_attribute("llm.model", provider)
        span.set_attribute("doc.ids",   str(id_list))
    try:
        result    = rag_engine.ask_chatbot(query, user_id=current_user.id, provider=provider, db=db, document_ids=id_list)
        latency_s = time.time() - start
        MODEL_LATENCY.labels(provider=provider).observe(latency_s)

        rag_q = RagQuery(
            user_id      = current_user.id,
            document_id  = primary_doc_id,
            document_ids = id_list,
            query        = query,
            response     = result["answer"],
            sources      = result.get("sources"),
            model_used   = provider,
            latency_ms   = int(latency_s * 1000),
            input_tokens = result.get("input_tokens"),
            output_tokens= result.get("output_tokens"),
            status       = "success",
        )
        db.add(rag_q)
        db.commit()
        logger.info(
            f"채팅 완료 | 모델={provider} latency={int(latency_s*1000)}ms",
            extra={"tags": {"user_id": str(current_user.id), "model": provider, "event": "chat"}},
        )
        return {"response": result["answer"], "model": provider, "sources": result["sources"]}

    except ModelAPIError as e:
        rag_q = RagQuery(
            user_id      = current_user.id,
            document_id  = primary_doc_id,
            document_ids = id_list,
            query        = query,
            response     = None,
            model_used   = provider,
            status       = e.error_code,
        )
        db.add(rag_q)
        db.commit()

        status_map = {"rate_limit": 429, "not_found": 404, "bad_request": 400}
        http_code  = status_map.get(e.error_code, 500)
        logger.warning(
            f"채팅 실패 | 모델={provider} code={e.error_code}: {e.message}",
            extra={"tags": {"user_id": str(current_user.id), "model": provider, "event": "chat_error"}},
        )
        raise HTTPException(status_code=http_code, detail={"error_code": e.error_code, "message": e.message})

    except Exception as e:
        logger.error(f"채팅 서버 오류 | 모델={provider}: {e}", extra={"tags": {"event": "chat_error"}})
        raise HTTPException(status_code=500, detail={"error_code": "error", "message": "서버 오류가 발생했습니다."})


@app.get("/api/chat/history")
async def get_chat_history(
    document_ids: str,          # 쉼표 구분 문자열 예) "1,2,3"
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not rag_engine.vector_store:
        doc_count = db.query(Document).count()
        if doc_count > 0:
            rag_engine.reset_and_rebuild_index(db)

    try:
        id_list = [int(x) for x in document_ids.split(",") if x.strip()]
    except ValueError:
        id_list = []

    history = (
        db.query(RagQuery)
        .filter(
            RagQuery.user_id    == current_user.id,
            RagQuery.document_id.in_(id_list),
            RagQuery.status     == "success",
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
                "model": q.model_used,
                "sources": q.sources  # DB에서 불러온 출처 정보 추가
            })
            
    return messages


@app.post("/api/questions/generate")
async def generate_questions(
    req: GenerateQuestionsRequest,
    provider: str = "openai",
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    owned = db.query(Document).filter(
        Document.id.in_(req.document_ids),
        Document.user_id == current_user.id,
    ).count()
    if owned != len(req.document_ids):
        raise HTTPException(status_code=404, detail="선택한 문서 중 존재하지 않는 문서가 있습니다.")

    try:
        start_time = time.time()
        raw = rag_engine.generate_questions(
            user_id=current_user.id, provider=provider, db=db, document_ids=req.document_ids
        )
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
            document_id    = req.document_ids[0],   # 대표 문서 ID
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
@app.get("/api/admin/queries")
async def admin_queries(
    search:   Optional[str] = None,
    model:    Optional[str] = None,
    user_id:  Optional[int] = None,
    page:     int = 1,
    limit:    int = 20,
    admin:    User = Depends(require_admin),
    db:       Session = Depends(get_db),
):
    """RAG 쿼리 로그 — 필터·페이지네이션 지원"""
    q = (
        db.query(RagQuery)
        .join(User,     RagQuery.user_id     == User.id)
        .join(Document, RagQuery.document_id == Document.id)
    )
    if model:
        # 정확한 매칭을 위해 대소문자 구분 없이 필터링 시도 가능 (여기선 일치로 설정)
        q = q.filter(RagQuery.model_used == model)
    if user_id:
        q = q.filter(RagQuery.user_id == user_id)
    if search:
        # 질문 내용 검색 강화 (ilike 사용)
        q = q.filter(RagQuery.query.ilike(f"%{search}%"))

    total = q.count()
    rows  = (
        q.order_by(RagQuery.queried_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "page":  page,
        "limit": limit,
        "items": [
            {
                "id":            r.id,
                "query":         r.query,
                "response":      r.response or "",
                "model":         r.model_used,
                "latency_ms":    r.latency_ms,
                "input_tokens":  r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens":  (r.input_tokens or 0) + (r.output_tokens or 0) if (r.input_tokens or r.output_tokens) else None,
                "queried_at":    r.queried_at.strftime("%Y-%m-%d %H:%M:%S") if r.queried_at else "-",
                "user_name":     r.user.name,
                "user_email":    r.user.email,
                "document_name": r.document.filename,
            }
            for r in rows
        ],
    }


@app.get("/api/admin/query-models")
async def admin_query_models(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """실제 쿼리에 사용된 고유 모델 목록 반환"""
    models = db.query(RagQuery.model_used).distinct().all()
    return [m[0] for m in models if m[0]]


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
    search: Optional[str] = None,
    page:   int = 1,
    limit:  int = 20,
    admin:  User = Depends(require_admin),
    db:     Session = Depends(get_db),
):
    """전체 사용자 목록 + 권한 — admin 전용"""
    q = db.query(User)
    if search:
        q = q.filter(
            (User.name.ilike(f"%{search}%")) | (User.email.ilike(f"%{search}%"))
        )
    total = q.count()
    users = q.order_by(User.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "total": total,
        "page":  page,
        "limit": limit,
        "items": [
            {
                "id":         u.id,
                "email":      u.email,
                "name":       u.name,
                "role":       u.role,
                "is_deleted": u.is_deleted,
                "deleted_at": u.deleted_at.strftime("%Y-%m-%d %H:%M") if u.deleted_at else None,
                "created_at": u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else None,
            }
            for u in users
        ],
    }


@app.patch("/api/admin/users/{user_id}/deactivate")
async def admin_deactivate_user(
    user_id: int,
    admin:   User = Depends(require_admin),
    db:      Session = Depends(get_db),
):
    """사용자 강제 탈퇴 (소프트 삭제) — admin 전용"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="자기 자신은 비활성화할 수 없습니다.")
    target.is_deleted = True
    target.deleted_at = get_kst_now()
    db.commit()
    return {"message": f"{target.email} 계정이 비활성화되었습니다."}


@app.patch("/api/admin/users/{user_id}/activate")
async def admin_activate_user(
    user_id: int,
    admin:   User = Depends(require_admin),
    db:      Session = Depends(get_db),
):
    """비활성화된 사용자 복구 — admin 전용"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    target.is_deleted = False
    target.deleted_at = None
    db.commit()
    return {"message": f"{target.email} 계정이 활성화되었습니다."}


@app.delete("/api/me")
async def self_deactivate(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """본인 탈퇴 (소프트 삭제)"""
    current_user.is_deleted = True
    current_user.deleted_at = get_kst_now()
    db.commit()
    return {"message": "탈퇴 처리가 완료되었습니다."}


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
    try:
        eval_avg = db.query(
            func.avg(RagEvaluation.faithfulness),
            func.avg(RagEvaluation.answer_relevancy),
            func.avg(RagEvaluation.context_precision),
            func.avg(RagEvaluation.context_recall),
        ).first()
    except Exception as e:
        print(f"DEBUG: RagEvaluation query failed: {e}")
        eval_avg = (0, 0, 0, 0)

    def pct(val):
        try:
            return round((float(val) if val is not None else 0) * 100, 1)
        except:
            return 0.0

    # ── 최근 쿼리 5건 ──
    recent_queries = []
    try:
        recent_queries = (
            db.query(RagQuery)
            .order_by(RagQuery.queried_at.desc())
            .limit(5)
            .all()
        )
    except Exception as e:
        print(f"DEBUG: Recent queries fetch failed: {e}")

    # ── 문서별 쿼리 수 ──
    doc_stats = []
    try:
        doc_stats = (
            db.query(Document.filename, func.count(RagQuery.id).label("cnt"))
            .outerjoin(RagQuery, Document.id == RagQuery.document_id)
            .group_by(Document.id, Document.filename)
            .all()
        )
    except Exception as e:
        print(f"DEBUG: Doc stats fetch failed: {e}")

    # ── 사용자별 쿼리 수 ──
    user_stats = []
    try:
        user_stats = (
            db.query(User.name, User.email, func.count(RagQuery.id).label("cnt"))
            .outerjoin(RagQuery, User.id == RagQuery.user_id)
            .group_by(User.id, User.name, User.email)
            .order_by(func.count(RagQuery.id).desc())
            .limit(10)
            .all()
        )
    except Exception as e:
        print(f"DEBUG: User stats fetch failed: {e}")

    # ── 모델별 성능 통계 ──
    model_performance = []
    try:
        model_performance = (
            db.query(
                RagQuery.model_used,
                func.count(RagQuery.id).label("total_calls"),
                func.avg(RagQuery.latency_ms).label("avg_latency")
            )
            .group_by(RagQuery.model_used)
            .all()
        )
    except Exception as e:
        print(f"DEBUG: Model performance fetch failed: {e}")

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
