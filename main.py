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
from sqlalchemy import func, text

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
    RagRetrievedChunk, RagEvaluation, RagGroundTruth,
    QuizAttempt, QuizQuestion, StudySession, get_kst_now
)
from backend.auth import get_current_user, require_admin, require_user

# LangChain
from langchain_community.document_loaders import PyPDFLoader, TextLoader, PyMuPDFLoader
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
                temperature=0.1,
                safety_settings={
                    "HARM_CATEGORY_HARASSMENT":       "BLOCK_NONE",
                    "HARM_CATEGORY_HATE_SPEECH":       "BLOCK_NONE",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
                    "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
                }
            )

        if provider == "groq" and GROQ_API_KEY:
            return ChatGroq(model_name="llama-3.1-8b-instant", groq_api_key=GROQ_API_KEY, temperature=0.1)

        if provider == "huggingface" and HUGGINGFACE_API_KEY and GROQ_API_KEY:
            return ChatGroq(model_name="llama-3.1-8b-instant", groq_api_key=GROQ_API_KEY, temperature=0.1)

        # ── Groq 추가 모델 (기존 키 재사용) ────
        if provider == "groq-70b" and GROQ_API_KEY:
            return ChatGroq(model_name="llama-3.3-70b-versatile", groq_api_key=GROQ_API_KEY, temperature=0.1)

        if provider == "groq-mixtral" and GROQ_API_KEY:
            return ChatGroq(model_name="moonshotai/kimi-k2-instruct", groq_api_key=GROQ_API_KEY, temperature=0.1)

        if provider == "groq-gemma" and GROQ_API_KEY:
            return ChatGroq(model_name="llama-3.1-8b-instant", groq_api_key=GROQ_API_KEY, temperature=0.1)

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

    @staticmethod
    def _is_garbled(text: str) -> bool:
        """한글/ASCII 비율이 낮으면 깨진 텍스트로 판단"""
        if not text:
            return True
        normal = sum(1 for c in text if '\uAC00' <= c <= '\uD7A3' or c.isascii())
        return normal / len(text) < 0.3

    def process_document(self, file_path: str, user_id: int, document_id: int = None, db: Session = None):
        docs = None
        if file_path.endswith(".pdf"):
            # 1차: PyMuPDF (한글 인코딩 처리 우수)
            try:
                docs = PyMuPDFLoader(file_path).load()
                sample = " ".join(d.page_content for d in docs[:3])
                if self._is_garbled(sample):
                    logger.warning(f"PyMuPDF 텍스트 깨짐 감지 — PyPDF로 재시도: {file_path}")
                    docs = None
            except Exception as e:
                logger.warning(f"PyMuPDF 로드 실패 — PyPDF로 재시도: {e}")

            # 2차 fallback: PyPDF
            if docs is None:
                try:
                    docs = PyPDFLoader(file_path).load()
                except Exception as e:
                    logger.error(f"PDF 로드 최종 실패 ({file_path}): {e}")
                    return
        else:
            try:
                docs = TextLoader(file_path, encoding="utf-8").load()
            except Exception:
                try:
                    docs = TextLoader(file_path, encoding="cp949").load()
                except Exception as e:
                    logger.error(f"텍스트 로드 실패 ({file_path}): {e}")
                    return

        if not docs:
            logger.error(f"파일 로드 결과 없음: {file_path}")
            return

        # 메타데이터에 정보 주입 (보안 및 필터용)
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

        # 사용자 격리 + 선택한 문서 필터
        doc_ids_set = set(document_ids) if document_ids else None
        def _filter(meta):
            if meta.get("user_id") != user_id:
                return False
            if doc_ids_set:
                return meta.get("document_id") in doc_ids_set
            return True

        # 1. 초기 검색 (k=10으로 충분히 가져옴)
        try:
            candidates = self.vector_store.similarity_search_with_score(query, k=10, filter=_filter)
        except Exception:
            plain = self.vector_store.similarity_search(query, k=10, filter=_filter)
            candidates = [(d, 0.0) for d in plain]

        if not candidates:
            return {"answer": "관련된 내용을 찾을 수 없습니다.", "sources": []}

        # 2. LLM 리랭킹 (검색 품질 강화)
        rerank_prompt = (
            "당신은 정보 분석 전문가입니다. 아래 [문서 목록] 중에서 [질문]에 답하는 데 가장 직접적인 도움이 되는 문서 3개의 번호를 골라주세요.\n"
            "번호만 쉼표로 구분해 답하세요. 예: 1, 4, 10\n\n"
            f"[질문]: {query}\n\n"
            "[문서 목록]\n"
        )
        for i, (doc, _) in enumerate(candidates, 1):
            rerank_prompt += f"{i}. (페이지: {doc.metadata.get('page', '?')}) {doc.page_content[:150]}...\n"

        try:
            rerank_res = llm.invoke(rerank_prompt).content
            selected_indices = [int(idx.strip()) - 1 for idx in re.findall(r'\d+', rerank_res) if 0 < int(idx.strip()) <= len(candidates)]
            if not selected_indices: selected_indices = [0, 1, 2]
        except:
            selected_indices = [0, 1, 2]

        retrieved = [candidates[i][0] for i in selected_indices[:4]]
        sources   = list(set(f"{os.path.basename(d.metadata.get('source', '알 수 없음'))} (p.{d.metadata.get('page', '?')})" for d in retrieved))
        context   = "\n".join([f"[출처: {d.metadata.get('source')} Page: {d.metadata.get('page')}]\n{d.page_content}" for d in retrieved])

        # 3. 최종 답변 생성 (자연스러운 본문 중심 프롬프트)
        prompt = (
            "당신은 문서 기반 전문 상담원입니다. 아래 [제공된 컨텍스트]를 바탕으로 질문에 한국어로 상세히 답해주세요.\n"
            "### 규칙 ###\n"
            "1. 문서에 명시된 내용을 바탕으로 정확하게 설명하세요.\n"
            "2. **중요: 답변 본문에 '문서의 ~페이지', '어느 섹션에 따르면'과 같은 출처 언급을 절대 하지 마세요.**\n"
            "3. 답변은 자연스러운 설명조로 작성하고, 출처 표시는 생략하세요. (출처는 시스템이 별도로 표시합니다.)\n"
            "4. 답변이 모호하거나 문서에 없는 내용이면 솔직하게 모른다고 하세요.\n\n"
            f"[제공된 컨텍스트]\n{context}\n\n질문: {query}"
        )

        try:
            response_obj  = llm.invoke(prompt)
            answer        = response_obj.content
            usage         = getattr(response_obj, "usage_metadata", None) or {}
            return {"answer": answer, "sources": sources,
                    "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
                    "output_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
                    "retrieved_with_scores": candidates,
                    "context": context}
        except Exception as e:
            # (기존 에러 처리 로직 유지)
            raise e

    def generate_questions(self, user_id: int, provider: str = "openai",
                           db: Session = None, document_ids: list = None) -> str:
        if not self.vector_store and db:
            if db.query(Document).count() > 0:
                self.reset_and_rebuild_index(db)

        if not self.vector_store: return "[]"
        llm = self._get_llm(provider)
        if not llm: return "[]"

        # 1. 문서 핵심 키워드 추출 (중요도 순)
        doc_ids_set = set(document_ids) if document_ids else None
        def _filter(meta):
            if meta.get("user_id") != user_id: return False
            if doc_ids_set: return meta.get("document_id") in doc_ids_set
            return True

        # 전체적인 맥락 파악을 위해 고르게 검색
        summary_docs = self.vector_store.similarity_search("핵심 주제와 주요 개념", k=15, filter=_filter)
        all_text = "\n".join(d.page_content for d in summary_docs)
        
        keyword_prompt = (
            "아래 [문서 내용]에서 학습자가 반드시 알아야 할 핵심 키워드 5개를 중요도 순으로 추출하세요.\n"
            "키워드만 쉼표로 구분해서 출력하세요. 예: 키워드1, 키워드2, 키워드3, 키워드4, 키워드5\n\n"
            f"[문서 내용]\n{all_text[:4000]}"
        )
        try:
            keywords_res = llm.invoke(keyword_prompt).content
            keywords = [k.strip() for k in keywords_res.split(",") if k.strip()][:5]
        except:
            keywords = ["주요 개념"]

        # 2. 각 키워드별로 1문제씩 생성 (Few-shot 적용)
        all_quizzes = []
        all_retrieved = []

        for kw in keywords:
            # 키워드별 관련 문서 검색
            kw_docs = self.vector_store.similarity_search(kw, k=3, filter=_filter)
            kw_context = "\n".join(d.page_content for d in kw_docs)
            all_retrieved.extend(kw_docs)

            quiz_prompt = (
                "당신은 전문 출제 위원입니다. 아래 [문서 내용]에 등장하는 핵심 키워드 **'"+kw+"'**에 대한 객관식 문제를 1개 만드세요.\n"
                "### 규칙 ###\n"
                "1. 반드시 문서에 근거한 사실만을 문제로 내세요.\n"
                "2. 정답은 4개의 옵션 중 하나여야 합니다.\n"
                "3. 문제, 옵션, 정답, 힌트를 포함한 JSON 객체 하나만 출력하세요.\n\n"
                "### 예시 (Few-shot) ###\n"
                '{"question": "이 문서에서 설명하는 A의 정의는?", "options": ["정의1", "정의2", "정의3", "정의4"], "answer": "정의1", "hint": "정의1은 ~와 관련이 있습니다."}\n\n'
                f"[문서 내용]\n{kw_context}"
            )
            
            try:
                res = llm.invoke(quiz_prompt).content
                # JSON 추출 시도
                match = re.search(r'\{.*\}', res, re.DOTALL)
                if match:
                    all_quizzes.append(json.loads(match.group()))
            except:
                continue

        return json.dumps(all_quizzes, ensure_ascii=False), all_retrieved



rag_engine = RAGEngine()


# ─── RAG 품질 평가 헬퍼 (한국어 최적화) ──────────────────────

def _load_stopwords() -> frozenset:
    """backend/stopwords_ko.txt 에서 불용어 로드. 파일 없으면 빈 집합 반환."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend", "stopwords_ko.txt")
    if not os.path.exists(path):
        return frozenset()
    with open(path, encoding="utf-8") as f:
        return frozenset(
            line.strip() for line in f if line.strip()
        )

_STOPWORDS_KO: frozenset = _load_stopwords()


def _eval_clean(text: str) -> str:
    """공백·특수문자 제거, 소문자 변환 (평가용)"""
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', text).lower()


def _eval_words(text: str) -> list:
    """2글자 이상 단어 추출 후 불용어 제거"""
    tokens = re.findall(r'[가-힣a-zA-Z0-9]+', text)
    return [w for w in tokens if len(w) >= 2 and w not in _STOPWORDS_KO]


def _stem_match(word: str, source_clean: str) -> bool:
    """단어가 source에 완전 일치하거나, 어미 1~4글자 제거 후 포함되는지 확인.
    - '지도학습이란' → '지도학습이라' → '지도학습이' → '지도학습' 순으로 시도
    - '이에요'(3글자), '입니다'(3글자), '이라고'(3글자) 어미까지 처리
    - 어근이 최소 2글자 이상 남아야 의미 있음
    """
    w = _eval_clean(word)
    if not w:
        return False
    if w in source_clean:
        return True
    max_strip = min(5, len(w) - 1)
    for i in range(1, max_strip):
        root = w[:-i]
        if len(root) >= 2 and root in source_clean:
            return True
    return False


def _compute_quiz_faithfulness(question: str, answer: str, chunk_content: str | None) -> float | None:
    """문제+정답 키워드가 출처 청크에 얼마나 포함됐는지 (0.0~1.0).
    불용어 제거 + 어미 어근 매칭 적용.
    """
    if not chunk_content or not question:
        return None
    words = _eval_words(question + " " + answer)
    if not words:
        return None
    chunk_clean = _eval_clean(chunk_content)
    matched = sum(1 for w in words if _stem_match(w, chunk_clean))
    return round(min(matched / len(words), 1.0), 4)


def _compute_simple_evaluation(query: str, context_docs: list, response: str) -> dict:
    """불용어 제거 + 한국어 어근 매칭 기반 RAG 품질 지표 계산."""
    context_full   = " ".join(d.page_content for d in context_docs)
    context_clean  = _eval_clean(context_full)
    response_clean = _eval_clean(response)

    # ── faithfulness: 응답 단어가 컨텍스트에 얼마나 포함됐는가
    resp_words = _eval_words(response)
    faithfulness = (
        sum(1 for w in resp_words if _stem_match(w, context_clean)) / len(resp_words)
        if resp_words else 0.0
    )

    # ── answer_relevancy: 질문 키워드가 응답에 얼마나 포함됐는가
    query_words = _eval_words(query)
    answer_relevancy = (
        sum(1 for w in query_words if _stem_match(w, response_clean)) / len(query_words)
        if query_words else 0.0
    )

    # ── context_precision: 검색된 각 청크가 질문 키워드를 담고 있는가 (기여 청크 비율)
    if context_docs and query_words:
        contributing = sum(
            1 for d in context_docs
            if any(_stem_match(qw, _eval_clean(d.page_content)) for qw in query_words)
        )
        context_precision = contributing / len(context_docs)
    else:
        context_precision = 0.0

    return {
        "faithfulness":      round(min(faithfulness,      1.0), 4),
        "answer_relevancy":  round(min(answer_relevancy,  1.0), 4),
        "context_precision": round(min(context_precision, 1.0), 4),
        "context_recall":    None,
    }


def _save_retrieved_chunks(db: Session, query_id: int, retrieved_with_scores: list):
    """검색된 청크를 rag_retrieved_chunks 테이블에 저장"""
    for rank, (doc, score) in enumerate(retrieved_with_scores, 1):
        doc_id = doc.metadata.get("document_id")
        if not doc_id:
            continue
        # content로 DB 청크 매칭
        db_chunk = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc_id,
            DocumentChunk.content == doc.page_content,
        ).first()
        if db_chunk:
            db.add(RagRetrievedChunk(
                query_id         = query_id,
                chunk_id         = db_chunk.id,
                similarity_score = float(score),
                rank             = rank,
            ))


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
    return await oauth.google.authorize_redirect(request, redirect_uri, prompt="select_account")


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

    # 최근 로그인 일시 기록
    user.last_login_at = get_kst_now()
    db.commit()

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

    # ── 최근 학습 세션 5건 ──
    session_rows = (
        db.query(StudySession, Document)
        .join(Document, StudySession.document_id == Document.id)
        .filter(StudySession.user_id == current_user.id)
        .order_by(StudySession.last_accessed_at.desc())
        .limit(5)
        .all()
    )
    study_sessions = [
        {
            "document_id":      s.document_id,
            "filename":         doc.filename,
            "started_at":       s.started_at.strftime("%Y-%m-%d") if s.started_at else "-",
            "last_accessed_at": s.last_accessed_at.strftime("%Y-%m-%d %H:%M") if s.last_accessed_at else "-",
        }
        for s, doc in session_rows
    ]
    total_study_docs = db.query(StudySession.document_id).filter(
        StudySession.user_id == current_user.id
    ).distinct().count()

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
            "total_docs":       total_docs,
            "total_queries":    total_queries,
            "total_attempts":   total_attempts,
            "correct_count":    correct_count,
            "correct_rate":     correct_rate,
            "progress":         correct_rate,
            "total_study_docs": total_study_docs,
        },
        "model_usage":    model_usage,
        "recent_queries": recent_queries,
        "wrong_answers":  wrong_answers,
        "study_sessions": study_sessions,
    }


@app.get("/api/documents")
async def get_documents(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Document, func.count(DocumentChunk.id).label("chunk_count"))
        .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
        .filter(
            Document.user_id == current_user.id,
            Document.is_deleted.isnot(True),
        )
        .group_by(Document.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )
    return [
        {
            "id":          d.id,
            "filename":    d.filename,
            "file_type":   d.file_type,
            "chunk_count": chunk_count,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
        }
        for d, chunk_count in rows
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

    # ── StudySession 업서트 (문서 접근 기록) ──
    for doc_id in id_list:
        sess = db.query(StudySession).filter(
            StudySession.user_id    == current_user.id,
            StudySession.document_id == doc_id,
        ).order_by(StudySession.last_accessed_at.desc()).first()
        if sess:
            sess.last_accessed_at = get_kst_now()
        else:
            db.add(StudySession(user_id=current_user.id, document_id=doc_id))
    db.commit()

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
        db.refresh(rag_q)

        # ── RagRetrievedChunk 저장 ──
        retrieved_with_scores = result.get("retrieved_with_scores", [])
        try:
            _save_retrieved_chunks(db, rag_q.id, retrieved_with_scores)
            db.commit()
        except Exception as _e:
            db.rollback()
            logger.warning(f"RagRetrievedChunk 저장 실패 (무시): {_e}")

        # ── RagEvaluation 자동 계산·저장 ──
        try:
            context_docs = [doc for doc, _ in retrieved_with_scores]
            eval_scores  = _compute_simple_evaluation(query, context_docs, result["answer"])
            db.add(RagEvaluation(
                query_id          = rag_q.id,
                faithfulness      = eval_scores["faithfulness"],
                answer_relevancy  = eval_scores["answer_relevancy"],
                context_precision = eval_scores["context_precision"],
                context_recall    = eval_scores["context_recall"],
            ))
            db.commit()
        except Exception as _e:
            db.rollback()
            logger.warning(f"RagEvaluation 저장 실패 (무시): {_e}")

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
        raw, gen_retrieved = rag_engine.generate_questions(
            user_id=current_user.id, provider=provider, db=db, document_ids=req.document_ids
        )
        # 지연 시간 기록
        MODEL_LATENCY.labels(provider=provider).observe(time.time() - start_time)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 모델 응답 오류: {str(e)}")

    # 문제 생성에 사용된 청크를 DB에서 조회 (chunk_id 연결용)
    gen_chunk_ids: list[int | None] = []
    for doc in gen_retrieved:
        doc_id = doc.metadata.get("document_id")
        if doc_id:
            db_chunk = db.query(DocumentChunk).filter(
                DocumentChunk.document_id == doc_id,
                DocumentChunk.content == doc.page_content,
            ).first()
            gen_chunk_ids.append(db_chunk.id if db_chunk else None)
        else:
            gen_chunk_ids.append(None)

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
    for qi, q in enumerate(questions_data):
        # 데이터 구조 보정 — dict 형태와 list 형태 모두 처리
        if isinstance(q, dict):
            question_text = q.get("question", "")
            choices       = q.get("options") or q.get("choices") or []
            answer        = q.get("answer", "")
            hint          = q.get("hint", "")
        elif isinstance(q, list) and len(q) >= 3:
            # ["질문", ["A","B","C","D"], "정답"] 형태 대응
            question_text = q[0] if isinstance(q[0], str) else ""
            choices       = q[1] if isinstance(q[1], list) else []
            answer        = q[2] if isinstance(q[2], str) else ""
            hint          = q[3] if len(q) > 3 and isinstance(q[3], str) else ""
        else:
            continue

        if not question_text or not choices: continue

        # 문제를 생성에 기여한 청크와 연결 (순서 기준 순환 할당)
        chunk_id = gen_chunk_ids[qi % len(gen_chunk_ids)] if gen_chunk_ids else None

        # 출처 청크 내용 조회 (신뢰도 계산용)
        chunk_content = None
        if chunk_id:
            db_chunk_obj = db.query(DocumentChunk).filter(DocumentChunk.id == chunk_id).first()
            if db_chunk_obj:
                chunk_content = db_chunk_obj.content

        faithfulness_score = _compute_quiz_faithfulness(question_text, str(answer), chunk_content)

        qq = QuizQuestion(
            document_id        = req.document_ids[0],   # 대표 문서 ID
            chunk_id           = chunk_id,
            question           = question_text,
            choices            = choices,
            correct_answer     = str(answer),
            hint               = hint or None,
            faithfulness_score = faithfulness_score,
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
            "id":                qq.id,
            "document_id":       qq.document_id,
            "question":          qq.question,
            "choices":           qq.choices,
            "hint":              qq.hint,
            "faithfulness_score": round(qq.faithfulness_score * 100, 1) if qq.faithfulness_score is not None else None,
            "status":            "solved" if qq.id in solved_ids else ("wrong" if qq.id in attempted_ids else "new")
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

    # quiz_attempts(풀이 이력) 먼저 삭제 → 그 다음 문제 삭제
    # (.delete()는 SQL 직접 실행이라 ORM cascade가 동작하지 않음)
    question_ids = [
        q.id for q in db.query(QuizQuestion.id)
        .filter(QuizQuestion.document_id == document_id).all()
    ]
    if question_ids:
        db.query(QuizAttempt).filter(QuizAttempt.question_id.in_(question_ids)).delete(synchronize_session=False)
        db.query(QuizQuestion).filter(QuizQuestion.document_id == document_id).delete(synchronize_session=False)
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

    user_ans = req.user_answer.strip().upper()  # 대문자로 통일
    correct_ans = qq.correct_answer.strip()
    
    # ── 유연한 채점 로직 ──
    is_correct = False
    
    # 1. 실제 텍스트가 정확히 일치하는지 (대소문자 무시)
    if user_ans == correct_ans.upper():
        is_correct = True
    
    # 2. 사용자가 'A', 'B', 'C', 'D' 기호를 보낸 경우 처리
    elif user_ans in ["A", "B", "C", "D", "1", "2", "3", "4"]:
        # 기호를 인덱스로 변환 (A=0, B=1...)
        mapping = {"A":0, "B":1, "C":2, "D":3, "1":0, "2":1, "3":2, "4":3}
        idx = mapping.get(user_ans)
        
        if idx is not None and idx < len(qq.choices):
            selected_text = qq.choices[idx].strip().upper()
            # 선택한 번호의 텍스트가 DB의 정답 텍스트와 일치하는지 확인
            if selected_text == correct_ans.upper():
                is_correct = True
            # 혹은 DB에 저장된 정답 자체가 'B'와 같은 기호일 경우를 대비
            elif user_ans == correct_ans.upper():
                is_correct = True

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


# ─── Study Session API ───────────────────────

@app.get("/api/study/sessions")
async def get_study_sessions(
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """사용자의 학습 세션 목록 (문서별 최근 접근 기준)"""
    rows = (
        db.query(StudySession, Document)
        .join(Document, StudySession.document_id == Document.id)
        .filter(StudySession.user_id == current_user.id)
        .order_by(StudySession.last_accessed_at.desc())
        .all()
    )
    return [
        {
            "document_id":      s.document_id,
            "filename":         doc.filename,
            "file_type":        doc.file_type,
            "started_at":       s.started_at.isoformat() if s.started_at else None,
            "last_accessed_at": s.last_accessed_at.isoformat() if s.last_accessed_at else None,
        }
        for s, doc in rows
    ]


@app.get("/api/documents/{doc_id}/chunks")
async def get_document_chunks(
    doc_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """문서의 청크 목록 조회"""
    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.user_id == current_user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없거나 권한이 없습니다.")

    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    return [
        {
            "id":          c.id,
            "chunk_index": c.chunk_index,
            "content":     c.content[:400] + ("…" if len(c.content) > 400 else ""),
            "token_count": c.token_count,
        }
        for c in chunks
    ]


# ─── RAG Evaluation API ──────────────────────

@app.get("/api/admin/evaluations")
async def admin_evaluations(
    page:  int = 1,
    limit: int = 20,
    admin: User = Depends(require_admin),
    db:    Session = Depends(get_db),
):
    """RAG 평가 이력 조회 — admin 전용"""
    total = db.query(RagEvaluation).count()
    rows  = (
        db.query(RagEvaluation, RagQuery)
        .join(RagQuery, RagEvaluation.query_id == RagQuery.id)
        .order_by(RagEvaluation.evaluated_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "page":  page,
        "items": [
            {
                "id":                ev.id,
                "query":             rq.query[:80] + ("…" if len(rq.query) > 80 else ""),
                "model":             rq.model_used,
                "faithfulness":      round((ev.faithfulness or 0) * 100, 1),
                "answer_relevancy":  round((ev.answer_relevancy or 0) * 100, 1),
                "context_precision": round((ev.context_precision or 0) * 100, 1),
                "context_recall":    round((ev.context_recall or 0) * 100, 1) if ev.context_recall is not None else None,
                "evaluated_at":      ev.evaluated_at.strftime("%Y-%m-%d %H:%M") if ev.evaluated_at else "-",
            }
            for ev, rq in rows
        ],
    }


@app.post("/api/admin/evaluations/recalculate")
async def recalculate_evaluations(
    admin: User = Depends(require_admin),
    db:    Session = Depends(get_db),
):
    """기존 RAG 평가 이력을 개선된 알고리즘으로 일괄 재계산 — admin 전용.
    answer_relevancy=0 이거나 context_precision=0 인 레코드를 대상으로 재계산.
    rag_retrieved_chunks 에 청크가 저장된 경우에만 재계산 가능.
    """
    # 재계산 대상: answer_relevancy=0 또는 context_precision=0
    targets = (
        db.query(RagEvaluation)
        .filter(
            (RagEvaluation.answer_relevancy == 0) | (RagEvaluation.context_precision == 0)
        )
        .all()
    )

    updated = 0
    skipped = 0  # 청크 정보 없어서 건너뜀

    for ev in targets:
        rq = db.query(RagQuery).filter(RagQuery.id == ev.query_id).first()
        if not rq:
            skipped += 1
            continue

        # 저장된 retrieved_chunks 로 context_docs 재구성
        retrieved = (
            db.query(RagRetrievedChunk, DocumentChunk)
            .join(DocumentChunk, RagRetrievedChunk.chunk_id == DocumentChunk.id)
            .filter(RagRetrievedChunk.query_id == ev.query_id)
            .order_by(RagRetrievedChunk.rank)
            .all()
        )
        if not retrieved:
            skipped += 1
            continue

        # LangChain Document 흉내 (page_content 속성만 필요)
        class _FakeDoc:
            def __init__(self, content):
                self.page_content = content

        context_docs = [_FakeDoc(chunk.content) for _, chunk in retrieved]

        scores = _compute_simple_evaluation(rq.query, context_docs, rq.response or "")

        ev.faithfulness      = scores["faithfulness"]
        ev.answer_relevancy  = scores["answer_relevancy"]
        ev.context_precision = scores["context_precision"]
        ev.evaluated_at      = get_kst_now()
        updated += 1

    db.commit()
    return {"updated": updated, "skipped": skipped, "total_targets": len(targets)}


# ─── Ground Truth API (Admin) ─────────────────

class GroundTruthRequest(BaseModel):
    document_id:  int
    question:     str
    ideal_answer: str


@app.get("/api/admin/ground-truths")
async def list_ground_truths(
    document_id: Optional[int] = None,
    page:  int = 1,
    limit: int = 20,
    admin: User = Depends(require_admin),
    db:    Session = Depends(get_db),
):
    """정답 데이터셋 목록 — admin 전용"""
    q = db.query(RagGroundTruth, Document).join(Document, RagGroundTruth.document_id == Document.id)
    if document_id:
        q = q.filter(RagGroundTruth.document_id == document_id)
    total = q.count()
    rows  = q.order_by(RagGroundTruth.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "total": total,
        "page":  page,
        "items": [
            {
                "id":           gt.id,
                "document_id":  gt.document_id,
                "filename":     doc.filename,
                "question":     gt.question,
                "ideal_answer": gt.ideal_answer,
                "created_at":   gt.created_at.strftime("%Y-%m-%d %H:%M") if gt.created_at else "-",
            }
            for gt, doc in rows
        ],
    }


@app.post("/api/admin/ground-truths")
async def create_ground_truth(
    req:   GroundTruthRequest,
    admin: User = Depends(require_admin),
    db:    Session = Depends(get_db),
):
    """정답 데이터 추가 — admin 전용"""
    doc = db.query(Document).filter(Document.id == req.document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    gt = RagGroundTruth(
        document_id  = req.document_id,
        created_by   = admin.id,
        question     = req.question,
        ideal_answer = req.ideal_answer,
    )
    db.add(gt)
    db.commit()
    db.refresh(gt)
    return {"id": gt.id, "message": "정답 데이터가 추가되었습니다."}


@app.delete("/api/admin/ground-truths/{gt_id}")
async def delete_ground_truth(
    gt_id: int,
    admin: User = Depends(require_admin),
    db:    Session = Depends(get_db),
):
    """정답 데이터 삭제 — admin 전용"""
    gt = db.query(RagGroundTruth).filter(RagGroundTruth.id == gt_id).first()
    if not gt:
        raise HTTPException(status_code=404, detail="정답 데이터를 찾을 수 없습니다.")
    db.delete(gt)
    db.commit()
    return {"message": "삭제되었습니다."}


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
    """본인 탈퇴 (소프트 삭제) — 관리자는 탈퇴 불가"""
    if current_user.role == UserRole.admin:
        raise HTTPException(status_code=400, detail="관리자 계정은 본인이 직접 탈퇴할 수 없습니다.")
    
    current_user.is_deleted = True
    current_user.deleted_at = get_kst_now()
    db.commit()
    return {"message": "탈퇴 처리가 완료되었습니다."}


@app.get("/api/admin/overview")
async def admin_overview(
    show_deleted: bool = False,
    admin: User = Depends(require_admin),
    db: Session  = Depends(get_db),
):
    """관리자 대시보드 종합 통계"""
    # ── 기본 카운트 (삭제되지 않은 문서/사용자 기준) ──
    total_users         = db.query(User).filter(User.is_deleted.isnot(True)).count()
    total_docs          = db.query(Document).filter(Document.is_deleted.isnot(True)).count()
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
        except Exception:
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

    # ── 문서별 쿼리 수 (document_ids JSONB 언네스트 기반 정확 집계) ──
    # document_ids([1,2,3]) 가 있는 쿼리 → 각 문서에 1씩 카운트
    # document_ids 가 null/빈 배열인 구버전 쿼리 → document_id(대표) 로 카운트
    doc_stats = []
    try:
        deleted_filter = "" if show_deleted else "AND (d.is_deleted = false OR d.is_deleted IS NULL)"
        doc_stats_sql = text(f"""
            WITH query_docs AS (
                -- document_ids 배열이 있는 쿼리: 배열 내 각 문서 ID에 카운트
                SELECT CAST(elem AS INTEGER) AS doc_id, rq.id AS query_id
                FROM rag_queries rq,
                     jsonb_array_elements_text(rq.document_ids) AS elem
                WHERE rq.document_ids IS NOT NULL
                  AND jsonb_typeof(rq.document_ids) = 'array'
                  AND jsonb_array_length(rq.document_ids) > 0

                UNION ALL

                -- document_ids 없는 구버전 쿼리: 대표 document_id 로 카운트
                SELECT rq.document_id AS doc_id, rq.id AS query_id
                FROM rag_queries rq
                WHERE rq.document_ids IS NULL
                   OR jsonb_typeof(rq.document_ids) != 'array'
                   OR jsonb_array_length(rq.document_ids) = 0
            )
            SELECT
                d.id,
                d.filename,
                d.is_deleted,
                u.name  AS user_name,
                u.email AS user_email,
                COUNT(qd.query_id) AS cnt
            FROM documents d
            LEFT JOIN query_docs qd ON qd.doc_id = d.id
            LEFT JOIN users u ON u.id = d.user_id
            WHERE 1=1 {deleted_filter}
            GROUP BY d.id, d.filename, d.is_deleted, u.name, u.email
            ORDER BY cnt DESC
            LIMIT 50
        """)
        rows = db.execute(doc_stats_sql).fetchall()
        doc_stats = rows
    except Exception as e:
        print(f"DEBUG: Doc stats fetch failed: {e}")

    # ── 사용자별 쿼리 수 TOP 10 ──
    user_stats = []
    try:
        user_stats = (
            db.query(User.name, User.email, func.count(RagQuery.id).label("cnt"))
            .outerjoin(RagQuery, User.id == RagQuery.user_id)
            .filter(User.is_deleted.isnot(True))
            .group_by(User.id, User.name, User.email)
            .order_by(func.count(RagQuery.id).desc())
            .limit(10)
            .all()
        )
    except Exception as e:
        print(f"DEBUG: User stats fetch failed: {e}")

    # ── 모델별 성능 통계 (호출 횟수 내림차순) ──
    model_performance = []
    try:
        model_performance = (
            db.query(
                RagQuery.model_used,
                func.count(RagQuery.id).label("total_calls"),
                func.avg(RagQuery.latency_ms).label("avg_latency"),
                func.min(RagQuery.latency_ms).label("min_latency"),
                func.max(RagQuery.latency_ms).label("max_latency"),
            )
            .group_by(RagQuery.model_used)
            .order_by(func.count(RagQuery.id).desc())  # 호출 많은 순
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
                "model":       m.model_used,
                "count":       m.total_calls,
                "avg_latency": round(m.avg_latency, 1) if m.avg_latency else 0,
                "min_latency": m.min_latency or 0,
                "max_latency": m.max_latency or 0,
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
        "doc_stats": [
            {
                "doc_id":      row.id,
                "filename":    row.filename,
                "is_deleted":  bool(row.is_deleted),
                "user_name":   row.user_name  or "알 수 없음",
                "user_email":  row.user_email or "",
                "query_count": row.cnt,
            }
            for row in doc_stats
        ],
        "user_stats": [{"name": u.name, "email": u.email, "query_count": u.cnt} for u in user_stats],
    }


@app.get("/api/admin/doc-stats")
async def admin_doc_stats(
    page:         int  = 1,
    limit:        int  = 20,
    search:       str  = "",          # 문서명 부분 검색
    user_email:   str  = "",          # 특정 사용자 필터
    show_deleted: bool = False,       # 삭제된 문서 포함 여부
    sort_by:      str  = "query_count",  # query_count | filename | user
    sort_dir:     str  = "desc",      # asc | desc
    admin: User = Depends(require_admin),
    db: Session  = Depends(get_db),
):
    """문서별 쿼리 현황 상세 페이지 — 필터·정렬·페이지네이션 지원"""
    # 정렬 컬럼 화이트리스트 (CTE 컬럼명 기준)
    sort_col = {
        "query_count": "cnt",
        "filename":    "filename",   # CTE 밖에서는 alias명 그대로
        "user":        "user_name",
    }.get(sort_by, "cnt")
    sort_direction = "ASC" if sort_dir == "asc" else "DESC"

    deleted_cond = "" if show_deleted else "AND (d.is_deleted = false OR d.is_deleted IS NULL)"
    search_cond  = "AND d.filename ILIKE :search"    if search     else ""
    user_cond    = "AND u.email    = :user_email"    if user_email else ""

    base_sql = f"""
        WITH query_docs AS (
            -- document_ids 배열이 있는 쿼리: 각 문서 ID에 1씩 카운트
            SELECT CAST(elem AS INTEGER) AS doc_id, rq.id AS query_id
            FROM rag_queries rq
            JOIN LATERAL jsonb_array_elements_text(rq.document_ids) AS elem ON true
            WHERE rq.document_ids IS NOT NULL
              AND jsonb_typeof(rq.document_ids) = 'array'
              AND jsonb_array_length(rq.document_ids) > 0

            UNION ALL

            -- document_ids 없는 구버전 쿼리: 대표 document_id 사용
            SELECT rq.document_id AS doc_id, rq.id AS query_id
            FROM rag_queries rq
            WHERE rq.document_ids IS NULL
               OR jsonb_typeof(rq.document_ids) != 'array'
               OR jsonb_array_length(rq.document_ids) = 0
        ),
        doc_counts AS (
            SELECT
                d.id,
                d.filename,
                COALESCE(d.is_deleted, false) AS is_deleted,
                d.uploaded_at,
                COALESCE(u.name,  '알 수 없음') AS user_name,
                COALESCE(u.email, '')            AS user_email,
                COUNT(qd.query_id)               AS cnt
            FROM documents d
            LEFT JOIN query_docs qd ON qd.doc_id = d.id
            LEFT JOIN users u       ON u.id = d.user_id
            WHERE 1=1
            {deleted_cond}
            {search_cond}
            {user_cond}
            GROUP BY d.id, d.filename, d.is_deleted, d.uploaded_at, u.name, u.email
        )
    """

    params: dict = {}
    if search:     params["search"]     = f"%{search}%"
    if user_email: params["user_email"] = user_email

    try:
        total: int = db.execute(
            text(base_sql + "SELECT COUNT(*) FROM doc_counts"), params
        ).scalar() or 0

        params["limit"]  = limit
        params["offset"] = (page - 1) * limit
        rows = db.execute(
            text(base_sql + f"SELECT * FROM doc_counts ORDER BY {sort_col} {sort_direction} LIMIT :limit OFFSET :offset"),
            params,
        ).fetchall()

        user_list = db.execute(text("""
            SELECT DISTINCT u.name, u.email
            FROM users u
            JOIN documents d ON d.user_id = u.id
            WHERE u.is_deleted IS NOT TRUE
            ORDER BY u.name
        """)).fetchall()

    except Exception as e:
        print(f"[doc-stats] SQL 오류: {e}")
        raise HTTPException(status_code=500, detail=f"쿼리 통계 조회 실패: {str(e)}")

    return {
        "total": total,
        "page":  page,
        "limit": limit,
        "items": [
            {
                "doc_id":      row.id,
                "filename":    row.filename,
                "is_deleted":  bool(row.is_deleted),
                "uploaded_at": row.uploaded_at.strftime("%Y-%m-%d") if row.uploaded_at else "-",
                "user_name":   row.user_name,
                "user_email":  row.user_email,
                "query_count": row.cnt,
            }
            for row in rows
        ],
        "user_list": [{"name": u.name, "email": u.email} for u in user_list],
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
    # API 경로는 여기에 도달하면 안 됨 — 404 반환
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API endpoint not found")
    file_path = os.path.join("frontend", path)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return FileResponse("frontend/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
