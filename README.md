# RAG Study Platform — AI 기반 학습 플랫폼

업로드한 문서(PDF·TXT)를 기반으로 **RAG(Retrieval-Augmented Generation)** 방식의 챗봇 학습, AI 문제 생성, 오답 복습을 제공하는 풀스택 학습 플랫폼입니다.  
10개 이상의 LLM을 런타임에 자유롭게 전환할 수 있으며, Prometheus·Loki·Tempo·Grafana를 통해 모델별 성능을 실시간 비교합니다.

---

## 목차

1. [시스템 아키텍처](#1-시스템-아키텍처)
2. [RAG 데이터 파이프라인](#2-rag-데이터-파이프라인)
3. [페이지 구성](#3-페이지-구성)
4. [지원 LLM 모델 비교](#4-지원-llm-모델-비교)
5. [DB 스키마](#5-db-스키마)
6. [관측성 인프라](#6-관측성-인프라-observability)
7. [기술 스택 및 패키지](#7-기술-스택--주요-패키지)
8. [설치 및 실행](#8-설치--실행)
9. [환경 변수](#9-환경-변수)

---

## 1. 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (SPA)                            │
│           Vue 3 + Vue Router + Axios + TailwindCSS              │
│                    http://localhost:8000                         │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTP REST
┌──────────────────────────▼──────────────────────────────────────┐
│                    FastAPI  (main.py)                            │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │  Auth Layer  │  │  RAG Engine  │  │  Admin API           │   │
│  │ Google OAuth │  │  (LangChain) │  │  /api/admin/*        │   │
│  │  Session Mgr │  │  FAISS Index │  │  사용자·쿼리 관리    │   │
│  └─────────────┘  └──────┬───────┘  └──────────────────────┘   │
│                           │                                      │
│  ┌────────────────────────▼───────────────────────────────────┐ │
│  │                   LLM Router                               │ │
│  │  OpenAI · Groq×4 · Mistral · HuggingFace · Ollama(로컬)   │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Observability                                           │   │
│  │  Prometheus Metrics  ·  Loki Logs  ·  Tempo Traces       │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ SQLAlchemy ORM
┌──────────────────────────▼──────────────────────────────────────┐
│               PostgreSQL (study_platform DB)                     │
│  users · documents · document_chunks · rag_queries              │
│  rag_retrieved_chunks · rag_evaluations · rag_ground_truths     │
│  quiz_questions · quiz_attempts · agent_sessions · study_sessions│
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│               Observability Stack  (Docker Compose)             │
│  Prometheus :9090  →  Grafana :3001                             │
│  Loki       :3100  →  Grafana                                   │
│  Tempo      :3200 / :4317 (OTLP gRPC)  →  Grafana              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. RAG 데이터 파이프라인

### 2-1. 문서 수집 (Ingestion)

```
파일 업로드 (PDF / TXT)
        │
        ▼
PyPDFLoader / TextLoader          ← langchain_community
        │
        ▼
RecursiveCharacterTextSplitter    ← chunk_size=1000, overlap=200
        │  메타데이터 주입: user_id, document_id
        ▼
HuggingFaceEmbeddings             ← paraphrase-multilingual-MiniLM-L12-v2
  (sentence-transformers, CPU)
        │
        ▼
FAISS Vector Store (in-memory)    ← 서버 재시작 시 DB 기반 자동 재구축
        │
        ▼
DocumentChunk 저장 (PostgreSQL)   ← chunk_index, content, token_count
```

### 2-2. 질문 응답 (Retrieval-Augmented Generation)

```
사용자 질문 입력
        │
        ▼
FAISS Similarity Search (k=3)
  └─ callable filter: user_id 일치 + 선택 document_id 포함
        │
        ▼
Retrieved Chunks → Context 조합
        │
        ▼
LLM 호출 (선택된 provider)
  ┌─────────────────────────────────────────────┐
  │  Prompt = 컨텍스트 + 사용자 질문 (한국어)   │
  └─────────────────────────────────────────────┘
        │
        ▼
응답 반환 + DB 저장 (RagQuery)
  └─ query, response, sources(JSONB), document_ids(JSONB),
     model_used, latency_ms, input_tokens, output_tokens,
     status (success | rate_limit | not_found | bad_request | error)
```

### 2-3. 문제 생성 파이프라인

```
선택된 문서 ID 목록
        │
        ▼
FAISS Retrieval (k=5, "주요 개념과 핵심 내용")
        │
        ▼
LLM → 객관식 5문제 JSON 생성
  └─ 정규식 추출 + ast.literal_eval 이중 파싱 (마크다운 펜스 제거)
        │
        ▼
QuizQuestion 저장 (question, choices JSON, correct_answer)
```

### 2-4. 사용자 격리 (Security)

- 모든 문서·질문·벡터 검색에 `user_id` 필터 적용
- FAISS callable filter로 다중 문서 선택 시 정확한 소유권 검증
- 소프트 삭제(`is_deleted`) 패턴 — 문서·사용자 모두 적용
- 문서 삭제 시 파일은 `temp/` 폴더로 이동, 벡터 인덱스 재구축

---

## 3. 페이지 구성

| 경로 | 컴포넌트 | 설명 |
|------|----------|------|
| `/` | Dashboard | 학습 통계(정답률·진도·문서수·질문수), 모델별 사용 현황 그래프, 최근 질문 내역 아코디언, 오답 요약 카드 |
| `/upload` | Upload | 다중 파일 드래그앤드롭 업로드, 업로드 문서 목록·체크박스 선택, 채팅/문제 생성 바로가기, 소프트 삭제 |
| `/chat` | Chat | 다중 문서 체크박스 선택, 모델 선택 드롭다운, 메시지 말풍선 UI, 출처(Sources) 표시, API 오류 팝업(429·404·400) |
| `/questions` | Questions | 문서 다중 선택 + 모델 선택으로 AI 문제 생성, 객관식 풀이·채점, 틀린 문제 필터(reviewMode), 문제 삭제 |
| `/review` | WrongAnswersReview | 전체 틀린 문제 재도전, 진행 바, 이전 답 힌트 없이 새로 풀기, 채점 후 정답 강조, 틀린 것만 재도전 |
| `/admin` | AdminDashboard | 전체 통계 요약 카드 5개, 모델별 성능 분석 테이블, RAG 평가 지표 도넛 차트, 사용자 등급 관리, 최근 쿼리 미리보기 |
| `/admin/logs` | QueryLogs | 전체 RAG 쿼리 로그 — 검색·모델·사용자 필터, 페이지네이션, 토큰·지연 시간 표시, 응답 내용 펼치기 |
| `/admin/users` | AdminUsers | 사용자 목록 검색·페이지네이션, 권한(role) 변경 드롭다운, 강제 탈퇴/복구 버튼, 가입일·탈퇴일 표시 |

### 공통 기능
- **Google OAuth 2.0** 소셜 로그인 (Authlib + Starlette Session)
- **사이드바**: 역할(admin/user)에 따라 관리자 메뉴 조건부 표시
- **회원 탈퇴**: 사이드바 하단 숨김 버튼 → 2단계 경고 모달 → 소프트 삭제 → 자동 로그아웃
- **모델 배지**: 모델별 색상 코드 일관 적용 (전 페이지 통일)

---

## 4. 지원 LLM 모델 비교

| Provider ID | 실제 모델 | 요금 | 특징 |
|-------------|-----------|------|------|
| `openai` | GPT-4o-mini | 유료 | 고품질·안정적, 최다 토큰 |
| `groq` | Llama 3.1 8B Instant | 무료 | ⚡ 초고속 (Groq 전용 추론 칩) |
| `groq-70b` | Llama 3.3 70B Versatile | 무료 | 고품질 대형 오픈소스 |
| `groq-mixtral` | Moonshotai Kimi K2 | 무료 | 긴 컨텍스트 처리 특화 |
| `groq-gemma` | Llama 3.1 8B Instant | 무료 | Groq 추가 슬롯 |
| `huggingface` | Llama 3.1 8B (Groq 경유) | 무료 | HF 키 소유자용 |
| `mistral` | Mistral Small Latest | 무료 티어 | 유럽산 경량 고성능 |
| `ollama` | 설정 가능 (기본: llama3.2) | 무료 | 🏠 완전 로컬, 인터넷 불필요 |

### 임베딩 모델 (고정)

| 용도 | 모델 | 비고 |
|------|------|------|
| 벡터 임베딩 | `paraphrase-multilingual-MiniLM-L12-v2` | sentence-transformers, CPU 실행, 50개 언어 지원 |

### 모델 자동 선택 로직

```
API 키 존재 여부 확인 → 사용 가능한 모델 목록 동적 구성
  → 없으면 미노출 (사용자 혼란 방지)
  → Fallback 순서: OpenAI → Groq
  → Ollama: 로컬 서버 ping으로 실행 여부 자동 감지 (timeout 1초)
```

### Grafana에서 모델 성능 비교 항목

| 비교 지표 | 수집 방법 |
|-----------|-----------|
| 호출 횟수 (calls/sec) | `rag_model_latency_seconds_count` |
| 평균 응답 시간 | `rag_model_latency_seconds_sum / count` |
| 누적 호출 수 | Stat 패널 절대값 |
| 입력·출력 토큰 수 | DB `rag_queries.input_tokens`, `output_tokens` |
| 오류율 | DB `rag_queries.status` 집계 |

---

## 5. DB 스키마

```
users
├── id, email, name
├── role: admin | user
├── is_deleted, deleted_at    ← 소프트 삭제
└── created_at (KST naive)

documents
├── id, user_id → users
├── filename, file_path, file_type
├── is_deleted                ← 소프트 삭제 (파일은 temp/ 이동)
└── uploaded_at

document_chunks
├── id, document_id → documents
├── chunk_index, content, token_count
└── embedding_id              ← FAISS 내부 ID

rag_queries
├── id, user_id → users, document_id → documents
├── query, response
├── sources (JSONB)           ← 참조 파일명 목록
├── document_ids (JSONB)      ← 다중 선택된 문서 ID 배열
├── model_used, latency_ms
├── input_tokens, output_tokens
├── status: success | rate_limit | not_found | bad_request | error
└── queried_at

rag_retrieved_chunks
├── query_id → rag_queries, chunk_id → document_chunks
├── similarity_score, rank

rag_evaluations               ← RAG 품질 평가 (admin 전용)
├── faithfulness              ← 답변이 컨텍스트에 충실한가
├── answer_relevancy          ← 답변이 질문과 관련 있는가
├── context_precision         ← 검색된 청크가 정확한가
└── context_recall            ← 필요한 청크가 빠짐없이 검색됐는가

rag_ground_truths             ← admin 작성 정답 데이터셋
├── document_id, created_by → users
├── question, ideal_answer

quiz_questions
├── id, document_id → documents, chunk_id → document_chunks
├── question, choices (JSON)  ← 4지선다 배열
└── correct_answer

quiz_attempts
├── id, user_id → users, question_id → quiz_questions
├── user_answer, is_correct
└── attempted_at

agent_sessions / agent_steps  ← Agent 기능 (향후 확장 예정)
study_sessions                ← 학습 진도 추적 (향후 확장 예정)
```

---

## 6. 관측성 인프라 (Observability)

### 수집 구조

```
FastAPI (main.py)
  │
  ├─ prometheus_fastapi_instrumentator (자동 수집)
  │     http_requests_total              ← 메서드·엔드포인트·상태코드별
  │     http_request_duration_seconds    ← 엔드포인트별 응답 시간
  │
  ├─ 커스텀 Histogram (main.py 직접 기록)
  │     rag_model_latency_seconds{provider}  ← 성공 응답 시에만 기록
  │
  ├─ python-logging-loki  →  Loki :3100
  │     서비스 태그: service=rag-backend
  │     이벤트 태그: event, user_id, model
  │     기록 항목: 업로드 완료, 채팅 완료, 채팅 오류
  │
  └─ OpenTelemetry SDK (OTLP gRPC)  →  Tempo :4317
        FastAPIInstrumentor → 모든 HTTP 요청 자동 span
        커스텀 span: upload_files, rag_chat
```

### Docker Compose 스택

| 서비스 | 이미지 | 포트 | 역할 |
|--------|--------|------|------|
| Prometheus | `prom/prometheus:v2.53.0` | 9090 | 메트릭 수집·저장 |
| Loki | `grafana/loki:3.0.0` | 3100 | 로그 집계·저장 |
| Tempo | `grafana/tempo:2.4.2` | 3200, 4317 | 분산 트레이싱 |
| Grafana | `grafana/grafana:11.5.2` | 3001 | 통합 시각화 대시보드 |

### Grafana 대시보드 패널 (자동 프로비저닝)

```
┌─────────────────────────────┬─────────────────────────────┐
│  모델별 호출 횟수 (req/sec) │  모델별 평균 응답시간 (Avg) │  ← 시계열
├─────────────────────────────┤                             │
│  모델별 누적 호출 수 (Stat) │                             │  ← 숫자 카드
├─────────────────────────────┬─────────────────────────────┤
│  HTTP 요청 처리율           │  HTTP 요청 평균 지연        │  ← 엔드포인트별
└─────────────────────────────┴─────────────────────────────┘
```

| 패널 | PromQL | 설명 |
|------|--------|------|
| 모델별 호출 횟수 | `rate(rag_model_latency_seconds_count[5m])` | 초당 API 호출 수, provider 레이블별 |
| 모델별 평균 응답시간 | `rate(..._sum[5m]) / rate(..._count[5m])` | 모델 간 속도 직접 비교 |
| 누적 호출 수 | `sum(..._count) by (provider)` | Stat 패널, 절대값 |
| HTTP 요청 처리율 | `rate(http_requests_total[5m])` | 엔드포인트·메서드·상태코드별 |
| HTTP 요청 지연 | `rate(http_request_duration_seconds_sum[5m]) / ...` | API 병목 탐지 |

- **접속**: `http://localhost:3001` (익명 Admin 권한 자동 부여)
- **새로고침**: 5초, **기본 조회 범위**: 최근 1시간
- **프로비저닝 파일**: `grafana_dashboard.json`, `grafana_datasources.yml`, `grafana_dashboards_provisioning.yml`

---

## 7. 기술 스택 · 주요 패키지

### Backend

| 분류 | 패키지 | 역할 |
|------|--------|------|
| 웹 프레임워크 | `fastapi`, `uvicorn` | ASGI 서버 |
| ORM / DB | `sqlalchemy`, `psycopg2-binary` | PostgreSQL 연동 |
| 인증 | `authlib`, `starlette` SessionMiddleware | Google OAuth 2.0 |
| RAG 프레임워크 | `langchain`, `langchain-community` | 체인·로더·스플리터 |
| 임베딩 | `langchain-huggingface`, `sentence-transformers` | 다국어 MiniLM (CPU) |
| 벡터스토어 | `faiss-cpu` | 인메모리, callable 필터 지원 |
| 문서 로더 | `pypdf`, `python-multipart` | PDF·TXT 처리 |
| LLM — OpenAI | `langchain-openai` | GPT-4o-mini |
| LLM — Groq | `langchain-groq` | Llama 3.1 8B / 3.3 70B / Kimi K2 |
| LLM — Mistral | `langchain-mistralai` | Mistral Small Latest |
| LLM — Ollama | `langchain-ollama` | 로컬 자체 실행 모델 |
| 메트릭 | `prometheus-fastapi-instrumentator` | HTTP 메트릭 자동 수집 |
| 트레이싱 | `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` | Tempo 연동 |
| 자동 계측 | `opentelemetry-instrumentation-fastapi` | FastAPI span 자동 생성 |
| 로그 | `python-logging-loki` | Loki 전송 |
| 유틸 | `python-dotenv`, `httpx` | 환경변수, HTTP 클라이언트 |

### Frontend (CDN, 빌드 단계 없음)

| 라이브러리 | 역할 |
|------------|------|
| Vue 3 (Composition API) | SPA 프레임워크 |
| Vue Router 4 | Hash 기반 클라이언트 라우팅 (`createWebHashHistory`) |
| Axios | REST API 통신 |
| TailwindCSS (CDN) | 유틸리티 퍼스트 스타일링 |

---

## 8. 설치 · 실행

### 사전 요구사항

- Python 3.10+
- PostgreSQL 실행 중 (기본: `localhost:5432`)
- (선택) Docker — 관측성 스택 실행 시

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

프로젝트 루트에 `.env` 파일 생성 → [환경 변수 섹션](#9-환경-변수) 참고

### 3. 서버 실행

```bash
python main.py
```

서버 시작 시 자동 처리:

- PostgreSQL `study_platform` DB 없으면 자동 생성
- `Base.metadata.create_all()` — 테이블 자동 생성
- 멱등 마이그레이션 (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)
- HuggingFace 임베딩 모델 로컬 캐시 로드 (최초 실행 시 다운로드)

접속: `http://localhost:8000`

### 4. 관측성 스택 실행 (선택)

```bash
docker-compose -f docker-compose.monitoring.yml up -d
```

| 서비스 | URL |
|--------|-----|
| Grafana 대시보드 | http://localhost:3001 |
| Prometheus | http://localhost:9090 |
| Loki | http://localhost:3100 |
| Tempo | http://localhost:3200 |

---

## 9. 환경 변수

```env
# ── LLM API Keys ──
OPENAI_API_KEY=sk-...           # GPT-4o-mini
GROQ_API_KEY=gsk_...            # Llama 3.1/3.3 8B·70B, Kimi K2
HUGGINGFACE_API_KEY=hf_...      # HuggingFace (Groq 경유 실행)
MISTRAL_API_KEY=...             # Mistral Small Latest

# ── Ollama (로컬 실행 시) ──
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2

# ── Google OAuth 2.0 (로그인 필수) ──
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
SECRET_KEY=any-random-secret-key

# ── Database ──
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/study_platform

# ── Observability (선택, Docker 스택 실행 시) ──
LOKI_URL=http://localhost:3100
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

> **최소 실행 조건**: `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` + `SECRET_KEY` + `DATABASE_URL` + LLM 키 1개 이상
