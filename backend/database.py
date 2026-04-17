import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# 한국어 Windows에서 %APPDATA% 경로(로밍)가 psycopg2 pgpass 파싱 오류를 일으키므로 비활성화
os.environ["PGPASSFILE"] = "NUL"

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/study_platform")


def _ensure_database_exists():
    """study_platform DB가 없으면 자동 생성"""
    from urllib.parse import urlparse
    parsed = urlparse(DATABASE_URL)
    db_name = parsed.path.lstrip("/")
    # postgres 기본 DB에 접속해서 대상 DB 존재 여부 확인 후 생성
    base_url = DATABASE_URL.replace(f"/{db_name}", "/postgres")
    base_engine = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with base_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_name}
        ).fetchone()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            print(f"[DB] '{db_name}' 데이터베이스를 생성했습니다.")
        else:
            print(f"[DB] '{db_name}' 데이터베이스가 이미 존재합니다.")
    base_engine.dispose()


_ensure_database_exists()

from backend.models import Base  # noqa: E402 (DB 생성 후 import)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
    # 기존 테이블에 누락된 컬럼 추가 (멱등 마이그레이션)
    _migrate()


def _migrate():
    migrations = [
        # ── 기존 컬럼 추가 ──
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS input_tokens  INTEGER",
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS output_tokens INTEGER",
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS status        VARCHAR DEFAULT 'success'",
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS sources       JSONB",
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS document_ids  JSONB",
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS cost          FLOAT DEFAULT 0.0",
        "ALTER TABLE documents   ADD COLUMN IF NOT EXISTS is_deleted    BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE users       ADD COLUMN IF NOT EXISTS is_deleted    BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE users       ADD COLUMN IF NOT EXISTS deleted_at    TIMESTAMP",
        "ALTER TABLE users       ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP",
        # ── quiz_questions chunk_id / hint / faithfulness_score ──
        "ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS chunk_id           INTEGER REFERENCES document_chunks(id)",
        "ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS created_at         TIMESTAMP",
        "ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS hint               TEXT",
        "ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS faithfulness_score FLOAT",
        # ── rag_retrieved_chunks ──
        """CREATE TABLE IF NOT EXISTS rag_retrieved_chunks (
            id               SERIAL PRIMARY KEY,
            query_id         INTEGER NOT NULL REFERENCES rag_queries(id) ON DELETE CASCADE,
            chunk_id         INTEGER NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
            similarity_score FLOAT,
            rank             INTEGER
        )""",
        # ── rag_evaluations ──
        """CREATE TABLE IF NOT EXISTS rag_evaluations (
            id                 SERIAL PRIMARY KEY,
            query_id           INTEGER NOT NULL UNIQUE REFERENCES rag_queries(id) ON DELETE CASCADE,
            faithfulness       FLOAT,
            answer_relevancy   FLOAT,
            context_precision  FLOAT,
            context_recall     FLOAT,
            evaluated_at       TIMESTAMP
        )""",
        # ── rag_ground_truths ──
        """CREATE TABLE IF NOT EXISTS rag_ground_truths (
            id           SERIAL PRIMARY KEY,
            document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            created_by   INTEGER NOT NULL REFERENCES users(id),
            question     TEXT NOT NULL,
            ideal_answer TEXT NOT NULL,
            created_at   TIMESTAMP
        )""",
        # ── study_sessions ──
        """CREATE TABLE IF NOT EXISTS study_sessions (
            id               SERIAL PRIMARY KEY,
            user_id          INTEGER NOT NULL REFERENCES users(id),
            document_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            started_at       TIMESTAMP,
            last_accessed_at TIMESTAMP
        )""",
        # ── agent_sessions / agent_steps ──
        """CREATE TABLE IF NOT EXISTS agent_sessions (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            goal        TEXT,
            status      VARCHAR DEFAULT 'running',
            started_at  TIMESTAMP,
            finished_at TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS agent_steps (
            id         SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            step_index INTEGER NOT NULL,
            step_type  VARCHAR,
            tool_name  VARCHAR,
            input      JSONB,
            output     JSONB,
            created_at TIMESTAMP
        )""",

        # ════════════════════════════════════════
        # 테이블 / 컬럼 설명 (COMMENT)
        # ════════════════════════════════════════

        # ── users ──
        "COMMENT ON TABLE  users                  IS '플랫폼 사용자 계정 (Google OAuth 로그인)'",
        "COMMENT ON COLUMN users.id               IS '사용자 기본키'",
        "COMMENT ON COLUMN users.email            IS 'Google 계정 이메일 (유니크, 로그인 식별자)'",
        "COMMENT ON COLUMN users.name             IS '사용자 표시 이름'",
        "COMMENT ON COLUMN users.role             IS '권한 등급: admin(관리자) / user(일반 사용자)'",
        "COMMENT ON COLUMN users.is_deleted       IS '탈퇴 여부 — true면 소프트 삭제 처리된 계정'",
        "COMMENT ON COLUMN users.deleted_at       IS '탈퇴 처리 일시 (탈퇴 전이면 NULL)'",
        "COMMENT ON COLUMN users.last_login_at    IS '가장 최근 로그인 일시'",
        "COMMENT ON COLUMN users.created_at       IS '계정 최초 생성 일시 (KST)'",

        # ── documents ──
        "COMMENT ON TABLE  documents              IS '사용자가 업로드한 학습 문서 (PDF·TXT 등)'",
        "COMMENT ON COLUMN documents.id           IS '문서 기본키'",
        "COMMENT ON COLUMN documents.user_id      IS '업로드한 사용자 ID (→ users.id)'",
        "COMMENT ON COLUMN documents.filename     IS '원본 파일명 (사용자에게 표시되는 이름)'",
        "COMMENT ON COLUMN documents.file_path    IS '서버 로컬 저장 경로'",
        "COMMENT ON COLUMN documents.file_type    IS '파일 형식 식별자 (pdf / txt 등)'",
        "COMMENT ON COLUMN documents.is_deleted   IS '소프트 삭제 여부 — true면 사용자에게 노출 안 됨'",
        "COMMENT ON COLUMN documents.uploaded_at  IS '업로드 일시 (KST)'",

        # ── document_chunks ──
        "COMMENT ON TABLE  document_chunks                IS '문서를 일정 크기로 분할한 청크 — RAG 검색의 기본 단위'",
        "COMMENT ON COLUMN document_chunks.id             IS '청크 기본키'",
        "COMMENT ON COLUMN document_chunks.document_id    IS '원본 문서 ID (→ documents.id)'",
        "COMMENT ON COLUMN document_chunks.chunk_index    IS '문서 내 청크 순서 (0부터 시작)'",
        "COMMENT ON COLUMN document_chunks.content        IS '청크 텍스트 원문'",
        "COMMENT ON COLUMN document_chunks.token_count    IS '청크 토큰 수 (LLM 컨텍스트 계산용)'",
        "COMMENT ON COLUMN document_chunks.embedding_id   IS 'FAISS 벡터 스토어 내 인덱스 ID'",

        # ── rag_queries ──
        "COMMENT ON TABLE  rag_queries                IS 'RAG 챗봇 질의·응답 이력'",
        "COMMENT ON COLUMN rag_queries.id             IS '쿼리 기본키'",
        "COMMENT ON COLUMN rag_queries.user_id        IS '질문한 사용자 ID (→ users.id)'",
        "COMMENT ON COLUMN rag_queries.document_id    IS '대표 문서 ID — 단일 선택 또는 구버전 쿼리에서만 사용'",
        "COMMENT ON COLUMN rag_queries.query          IS '사용자 질문 원문'",
        "COMMENT ON COLUMN rag_queries.response       IS 'LLM이 생성한 답변 원문'",
        "COMMENT ON COLUMN rag_queries.sources        IS '답변에 참고된 문서명 목록 (JSONB 배열, [\"파일명\", ...])'",
        "COMMENT ON COLUMN rag_queries.document_ids   IS '사용자가 선택한 문서 ID 목록 (JSONB 배열, 다중 선택 지원)'",
        "COMMENT ON COLUMN rag_queries.model_used     IS '응답 생성에 사용된 LLM 모델명 (gpt-4o-mini 등)'",
        "COMMENT ON COLUMN rag_queries.latency_ms     IS '질의 시작부터 응답 완료까지 소요 시간 (밀리초)'",
        "COMMENT ON COLUMN rag_queries.input_tokens   IS 'LLM에 전달된 입력 토큰 수'",
        "COMMENT ON COLUMN rag_queries.output_tokens  IS 'LLM이 생성한 출력 토큰 수'",
        "COMMENT ON COLUMN rag_queries.status         IS '처리 결과: success / rate_limit / not_found / bad_request / error'",
        "COMMENT ON COLUMN rag_queries.queried_at     IS '질의 일시 (KST)'",

        # ── rag_retrieved_chunks ──
        "COMMENT ON TABLE  rag_retrieved_chunks                  IS 'RAG 쿼리 시 FAISS로 검색된 상위 청크 이력'",
        "COMMENT ON COLUMN rag_retrieved_chunks.id               IS '검색 청크 이력 기본키'",
        "COMMENT ON COLUMN rag_retrieved_chunks.query_id         IS '해당 쿼리 ID (→ rag_queries.id)'",
        "COMMENT ON COLUMN rag_retrieved_chunks.chunk_id         IS '검색된 청크 ID (→ document_chunks.id)'",
        "COMMENT ON COLUMN rag_retrieved_chunks.similarity_score IS '코사인 유사도 점수 (0.0~1.0, 1에 가까울수록 유사)'",
        "COMMENT ON COLUMN rag_retrieved_chunks.rank             IS '유사도 순위 (1 = 가장 유사한 청크)'",

        # ── rag_evaluations ──
        "COMMENT ON TABLE  rag_evaluations                   IS 'RAG 응답 품질 자동 평가 결과 — 쿼리 1건당 1행'",
        "COMMENT ON COLUMN rag_evaluations.id                IS '평가 기본키'",
        "COMMENT ON COLUMN rag_evaluations.query_id          IS '평가 대상 쿼리 ID (→ rag_queries.id, 유니크)'",
        "COMMENT ON COLUMN rag_evaluations.faithfulness      IS '충실도: 답변 키워드가 검색된 컨텍스트에 포함된 비율 (0.0~1.0)'",
        "COMMENT ON COLUMN rag_evaluations.answer_relevancy  IS '답변 연관성: 질문 키워드와 답변 키워드의 일치도 (0.0~1.0)'",
        "COMMENT ON COLUMN rag_evaluations.context_precision IS '컨텍스트 정밀도: 검색된 청크 중 실제로 유용한 청크 비율 (0.0~1.0)'",
        "COMMENT ON COLUMN rag_evaluations.context_recall    IS '컨텍스트 재현율: 정답 데이터 기준 필요한 청크가 검색된 비율 (0.0~1.0, ground truth 필요)'",
        "COMMENT ON COLUMN rag_evaluations.evaluated_at      IS '평가 수행 일시 (KST)'",

        # ── rag_ground_truths ──
        "COMMENT ON TABLE  rag_ground_truths                IS 'RAG 평가용 정답 데이터셋 — 관리자가 직접 작성'",
        "COMMENT ON COLUMN rag_ground_truths.id             IS '정답 데이터 기본키'",
        "COMMENT ON COLUMN rag_ground_truths.document_id    IS '해당 문서 ID (→ documents.id)'",
        "COMMENT ON COLUMN rag_ground_truths.created_by     IS '작성한 관리자 사용자 ID (→ users.id)'",
        "COMMENT ON COLUMN rag_ground_truths.question       IS '평가용 질문 (RAG에 실제로 던질 문장)'",
        "COMMENT ON COLUMN rag_ground_truths.ideal_answer   IS '이상적인 정답 텍스트 (context_recall 계산 기준)'",
        "COMMENT ON COLUMN rag_ground_truths.created_at     IS '정답 데이터 작성 일시 (KST)'",

        # ── quiz_questions ──
        "COMMENT ON TABLE  quiz_questions                    IS 'LLM이 문서 청크 기반으로 자동 생성한 객관식 문제 은행'",
        "COMMENT ON COLUMN quiz_questions.id                 IS '문제 기본키'",
        "COMMENT ON COLUMN quiz_questions.document_id        IS '문제 출제 근거 문서 ID (→ documents.id)'",
        "COMMENT ON COLUMN quiz_questions.chunk_id           IS '문제 출제 근거 청크 ID (→ document_chunks.id)'",
        "COMMENT ON COLUMN quiz_questions.question           IS '문제 내용 (질문 텍스트)'",
        "COMMENT ON COLUMN quiz_questions.choices            IS '객관식 선택지 목록 (JSONB 배열, [\"① ...\", \"② ...\", ...])'",
        "COMMENT ON COLUMN quiz_questions.correct_answer     IS '정답 선택지 (choices 배열의 원소 값과 일치)'",
        "COMMENT ON COLUMN quiz_questions.hint               IS '힌트 텍스트 — 정답을 직접 알려주지 않는 선에서 단서 제공'",
        "COMMENT ON COLUMN quiz_questions.faithfulness_score IS '문제+정답 핵심 키워드가 출처 청크에 포함된 비율 (0.0~1.0, 문제 품질 지표)'",
        "COMMENT ON COLUMN quiz_questions.created_at         IS '문제 생성 일시 (KST)'",

        # ── quiz_attempts ──
        "COMMENT ON TABLE  quiz_attempts                IS '사용자의 퀴즈 풀이 이력 — 맞힌 경우와 틀린 경우 모두 기록'",
        "COMMENT ON COLUMN quiz_attempts.id             IS '풀이 이력 기본키'",
        "COMMENT ON COLUMN quiz_attempts.user_id        IS '풀이한 사용자 ID (→ users.id)'",
        "COMMENT ON COLUMN quiz_attempts.question_id    IS '풀이한 문제 ID (→ quiz_questions.id)'",
        "COMMENT ON COLUMN quiz_attempts.user_answer    IS '사용자가 선택한 답 (선택지 텍스트 또는 번호)'",
        "COMMENT ON COLUMN quiz_attempts.is_correct     IS '정답 여부 (true = 정답, false = 오답)'",
        "COMMENT ON COLUMN quiz_attempts.attempted_at   IS '풀이 일시 (KST)'",

        # ── study_sessions ──
        "COMMENT ON TABLE  study_sessions                    IS '사용자의 문서 학습 세션 — 어떤 문서를 언제 학습했는지 추적'",
        "COMMENT ON COLUMN study_sessions.id                 IS '학습 세션 기본키'",
        "COMMENT ON COLUMN study_sessions.user_id            IS '학습한 사용자 ID (→ users.id)'",
        "COMMENT ON COLUMN study_sessions.document_id        IS '학습한 문서 ID (→ documents.id)'",
        "COMMENT ON COLUMN study_sessions.started_at         IS '학습 세션 최초 시작 일시 (KST)'",
        "COMMENT ON COLUMN study_sessions.last_accessed_at   IS '해당 문서 가장 최근 접근 일시 (KST)'",

        # ── agent_sessions ──
        "COMMENT ON TABLE  agent_sessions                IS 'AI 에이전트 실행 세션 — 목표(goal) 하나당 1행'",
        "COMMENT ON COLUMN agent_sessions.id             IS '에이전트 세션 기본키'",
        "COMMENT ON COLUMN agent_sessions.user_id        IS '에이전트를 실행한 사용자 ID (→ users.id)'",
        "COMMENT ON COLUMN agent_sessions.goal           IS '에이전트에게 부여된 목표 텍스트'",
        "COMMENT ON COLUMN agent_sessions.status         IS '실행 상태: running(실행중) / completed(완료) / failed(실패)'",
        "COMMENT ON COLUMN agent_sessions.started_at     IS '세션 시작 일시 (KST)'",
        "COMMENT ON COLUMN agent_sessions.finished_at    IS '세션 종료 일시 (KST, 실행 중이면 NULL)'",

        # ── agent_steps ──
        "COMMENT ON TABLE  agent_steps                IS '에이전트 세션의 각 실행 단계 로그'",
        "COMMENT ON COLUMN agent_steps.id             IS '단계 기본키'",
        "COMMENT ON COLUMN agent_steps.session_id     IS '해당 에이전트 세션 ID (→ agent_sessions.id)'",
        "COMMENT ON COLUMN agent_steps.step_index     IS '세션 내 단계 순서 (0부터 시작)'",
        "COMMENT ON COLUMN agent_steps.step_type      IS '단계 유형: think(추론) / tool_call(도구 호출) / respond(최종 응답)'",
        "COMMENT ON COLUMN agent_steps.tool_name      IS '호출된 도구 이름 (search_doc, generate_quiz 등, tool_call 단계에서만 사용)'",
        "COMMENT ON COLUMN agent_steps.input          IS '단계 입력 데이터 (JSONB)'",
        "COMMENT ON COLUMN agent_steps.output         IS '단계 출력 데이터 (JSONB)'",
        "COMMENT ON COLUMN agent_steps.created_at     IS '단계 생성 일시 (KST)'",
    ]
    with engine.begin() as conn:
        for sql in migrations:
            conn.execute(text(sql))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
