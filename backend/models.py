from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, JSON,
    ForeignKey, Enum as SAEnum
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta, timezone
import enum

Base = declarative_base()

def get_kst_now():
    # KST(UTC+9) 시간대의 naive datetime 반환
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class UserRole(str, enum.Enum):
    admin = "admin"
    user  = "user"

class AgentStepType(str, enum.Enum):
    think     = "think"
    tool_call = "tool_call"
    respond   = "respond"

class AgentStatus(str, enum.Enum):
    running   = "running"
    completed = "completed"
    failed    = "failed"


# ─────────────────────────────────────────────
# [1] CORE
# ─────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id         = Column(Integer, primary_key=True, index=True)
    email      = Column(String, unique=True, index=True, nullable=False)
    name       = Column(String, nullable=False)
    role       = Column(SAEnum(UserRole), default=UserRole.user, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)  # 탈퇴 여부 (소프트 삭제)
    deleted_at    = Column(DateTime, nullable=True)                  # 탈퇴 일시
    last_login_at = Column(DateTime, nullable=True)                  # 최근 로그인 일시
    created_at    = Column(DateTime, default=get_kst_now)


    # relationships
    documents      = relationship("Document",     back_populates="user")
    rag_queries    = relationship("RagQuery",     back_populates="user")
    quiz_attempts  = relationship("QuizAttempt",  back_populates="user")
    study_sessions = relationship("StudySession", back_populates="user")
    agent_sessions = relationship("AgentSession", back_populates="user")
    ground_truths  = relationship("RagGroundTruth", back_populates="created_by_user")


class Document(Base):
    __tablename__ = "documents"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename    = Column(String, nullable=False)
    file_path   = Column(String, nullable=False)
    file_type   = Column(String)                  # pdf / txt / etc.
    is_deleted  = Column(Boolean, default=False, nullable=False)
    uploaded_at = Column(DateTime, default=get_kst_now)

    user           = relationship("User",          back_populates="documents")
    chunks         = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
    rag_queries    = relationship("RagQuery",      back_populates="document", cascade="all, delete-orphan")
    quiz_questions = relationship("QuizQuestion",  back_populates="document", cascade="all, delete-orphan")
    study_sessions = relationship("StudySession",  back_populates="document", cascade="all, delete-orphan")
    ground_truths  = relationship("RagGroundTruth", back_populates="document", cascade="all, delete-orphan")


# ─────────────────────────────────────────────
# [2] RAG
# ─────────────────────────────────────────────

class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id           = Column(Integer, primary_key=True, index=True)
    document_id  = Column(Integer, ForeignKey("documents.id"), nullable=False)
    chunk_index  = Column(Integer, nullable=False)   # 문서 내 순서
    content      = Column(Text, nullable=False)
    token_count  = Column(Integer)
    embedding_id = Column(String)                    # FAISS 내부 ID

    document         = relationship("Document",       back_populates="chunks")
    quiz_questions   = relationship("QuizQuestion",   back_populates="chunk")
    retrieved_chunks = relationship("RagRetrievedChunk", back_populates="chunk")


class RagQuery(Base):
    __tablename__ = "rag_queries"

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    document_id   = Column(Integer, ForeignKey("documents.id"), nullable=False)
    query         = Column(Text, nullable=False)
    response     = Column(Text)
    sources      = Column(JSON)                      # 참고한 출처 파일명 목록
    document_ids = Column(JSON)                      # 선택된 전체 문서 ID 목록 (다중 선택 지원)
    model_used   = Column(String)                    # gpt-4o-mini 등

    latency_ms    = Column(Integer)
    input_tokens  = Column(Integer)                  # 입력 토큰 수
    output_tokens = Column(Integer)                  # 출력 토큰 수
    cost          = Column(Float, default=0.0)       # 소모 비용 (USD)
    status        = Column(String, default="success")  # success | rate_limit | not_found | bad_request | error
    queried_at    = Column(DateTime, default=get_kst_now)

    user             = relationship("User",     back_populates="rag_queries")
    document         = relationship("Document", back_populates="rag_queries")
    retrieved_chunks = relationship("RagRetrievedChunk", back_populates="query")
    evaluation       = relationship("RagEvaluation", back_populates="query", uselist=False)


class RagRetrievedChunk(Base):
    __tablename__ = "rag_retrieved_chunks"

    id               = Column(Integer, primary_key=True, index=True)
    query_id         = Column(Integer, ForeignKey("rag_queries.id"), nullable=False)
    chunk_id         = Column(Integer, ForeignKey("document_chunks.id"), nullable=False)
    similarity_score = Column(Float)
    rank             = Column(Integer)               # 1등, 2등, 3등

    query = relationship("RagQuery",      back_populates="retrieved_chunks")
    chunk = relationship("DocumentChunk", back_populates="retrieved_chunks")


# ─────────────────────────────────────────────
# [3] RAG 성능 평가 (admin 전용)
# ─────────────────────────────────────────────

class RagEvaluation(Base):
    __tablename__ = "rag_evaluations"

    id                 = Column(Integer, primary_key=True, index=True)
    query_id           = Column(Integer, ForeignKey("rag_queries.id"), nullable=False, unique=True)
    faithfulness       = Column(Float)   # 답변이 컨텍스트에 충실한가
    answer_relevancy   = Column(Float)   # 답변이 질문과 관련 있는가
    context_precision  = Column(Float)   # 검색된 청크가 정확한가
    context_recall     = Column(Float)   # 필요한 청크가 빠짐없이 검색됐는가
    evaluated_at       = Column(DateTime, default=get_kst_now)

    query = relationship("RagQuery", back_populates="evaluation")


class RagGroundTruth(Base):
    """평가용 정답 데이터셋 — admin이 직접 작성"""
    __tablename__ = "rag_ground_truths"

    id           = Column(Integer, primary_key=True, index=True)
    document_id  = Column(Integer, ForeignKey("documents.id"), nullable=False)
    created_by   = Column(Integer, ForeignKey("users.id"), nullable=False)
    question     = Column(Text, nullable=False)
    ideal_answer = Column(Text, nullable=False)
    created_at   = Column(DateTime, default=get_kst_now)

    document         = relationship("Document", back_populates="ground_truths")
    created_by_user  = relationship("User",     back_populates="ground_truths")


# ─────────────────────────────────────────────
# [4] QUIZ (문제은행)
# ─────────────────────────────────────────────

class QuizQuestion(Base):
    __tablename__ = "quiz_questions"

    id             = Column(Integer, primary_key=True, index=True)
    document_id    = Column(Integer, ForeignKey("documents.id"), nullable=False)
    chunk_id       = Column(Integer, ForeignKey("document_chunks.id"))
    question       = Column(Text, nullable=False)
    choices        = Column(JSON)                    # 객관식 선택지
    correct_answer = Column(String, nullable=False)
    hint               = Column(Text,  nullable=True)  # 힌트 (정답을 직접 알려주지 않는 선에서)
    faithfulness_score = Column(Float, nullable=True)  # 문제+정답 키워드가 출처 청크에 얼마나 포함됐는지 (0.0~1.0)
    created_at         = Column(DateTime, default=get_kst_now)

    document = relationship("Document",      back_populates="quiz_questions")
    chunk    = relationship("DocumentChunk", back_populates="quiz_questions")
    attempts = relationship("QuizAttempt",   back_populates="question", cascade="all, delete-orphan")


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_id  = Column(Integer, ForeignKey("quiz_questions.id"), nullable=False)
    user_answer  = Column(String)
    is_correct   = Column(Boolean)
    attempted_at = Column(DateTime, default=get_kst_now)

    user     = relationship("User",         back_populates="quiz_attempts")
    question = relationship("QuizQuestion", back_populates="attempts")


# ─────────────────────────────────────────────
# [5] AGENT
# ─────────────────────────────────────────────

class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    goal        = Column(Text)
    status      = Column(SAEnum(AgentStatus), default=AgentStatus.running)
    started_at  = Column(DateTime, default=get_kst_now)
    finished_at = Column(DateTime, nullable=True)

    user  = relationship("User",       back_populates="agent_sessions")
    steps = relationship("AgentStep",  back_populates="session")


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("agent_sessions.id"), nullable=False)
    step_index = Column(Integer, nullable=False)
    step_type  = Column(SAEnum(AgentStepType))       # think / tool_call / respond
    tool_name  = Column(String, nullable=True)        # search_doc, generate_quiz 등
    input      = Column(JSON)
    output     = Column(JSON)
    created_at = Column(DateTime, default=get_kst_now)

    session = relationship("AgentSession", back_populates="steps")


# ─────────────────────────────────────────────
# [6] STUDY (학습 진도)
# ─────────────────────────────────────────────

class StudySession(Base):
    __tablename__ = "study_sessions"

    id               = Column(Integer, primary_key=True, index=True)
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=False)
    document_id      = Column(Integer, ForeignKey("documents.id"), nullable=False)
    started_at       = Column(DateTime, default=get_kst_now)
    last_accessed_at = Column(DateTime, default=get_kst_now, onupdate=get_kst_now)

    user     = relationship("User",     back_populates="study_sessions")
    document = relationship("Document", back_populates="study_sessions")
