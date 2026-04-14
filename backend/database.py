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
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS input_tokens  INTEGER",
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS output_tokens INTEGER",
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS status        VARCHAR DEFAULT 'success'",
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS sources       JSONB",
        "ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS document_ids  JSONB",
        "ALTER TABLE documents   ADD COLUMN IF NOT EXISTS is_deleted    BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE users       ADD COLUMN IF NOT EXISTS is_deleted    BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE users       ADD COLUMN IF NOT EXISTS deleted_at    TIMESTAMP",
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
