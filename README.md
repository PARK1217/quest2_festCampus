# RAG Study Platform — AI 기반 학습 플랫폼

업로드한 문서(PDF·TXT)를 기반으로 **RAG(Retrieval-Augmented Generation)** 방식의 챗봇 학습, AI 문제 생성, 오답 복습을 제공하는 풀스택 학습 플랫폼입니다.  
10개 이상의 LLM을 런타임에 자유롭게 전환할 수 있으며, Prometheus·Loki·Tempo·Grafana를 통해 모델별 성능을 실시간 비교합니다.

---

## 목차

1. [시스템 아키텍처](#1-시스템-아키텍처)
2. [RAG 데이터 파이프라인](#2-rag-데이터-파이프라인)
3. [페이지 구성](#3-페이지-구성)
4. [API 엔드포인트](#4-api-엔드포인트)
5. [지원 LLM 모델 비교](#5-지원-llm-모델-비교)
6. [DB 스키마](#6-db-스키마)
7. [RAG 품질 평가 시스템](#7-rag-품질-평가-시스템)
8. [관측성 인프라](#8-관측성-인프라-observability)
9. [기술 스택 및 패키지](#9-기술-스택--주요-패키지)
10. [성능 개선 이력](#10-성능-개선-이력--트러블슈팅)
11. [설치 및 실행](#11-설치--실행)
12. [환경 변수](#12-환경-변수)

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
│  │      OpenAI  ·  Groq×4  ·  Mistral  ·  HuggingFace         │ │
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
PyMuPDFLoader / TextLoader        ← 한글 인코딩 안정성을 위해 PyMuPDF 우선 시도
  (실패 시 PyPDFLoader fallback)     PyPDF만 사용 시 일부 한글 PDF에서 텍스트 깨짐 발생
        │
        ▼
RecursiveCharacterTextSplitter    ← chunk_size=1000, overlap=200
        │  메타데이터 주입: user_id, document_id
        ▼
HuggingFaceEmbeddings             ← paraphrase-multilingual-MiniLM-L12-v2
  (sentence-transformers, CPU)       50개 언어 지원 · API 키 불필요 · 완전 무료
        │
        ▼
FAISS Vector Store (in-memory)    ← 서버 재시작 시 DB 기반 자동 재구축
        │
        ▼
DocumentChunk 저장 (PostgreSQL)   ← chunk_index, content, token_count, embedding_id
```

### 2-2. 질문 응답 (Retrieval-Augmented Generation)

```
사용자 질문 입력 (다중 문서 선택 가능)
        │
        ▼
FAISS Similarity Search (k=3)
  └─ callable filter: user_id 일치 + 선택 document_id 배열 포함
        │
        ▼
Retrieved Chunks → Context 조합
  └─ RagRetrievedChunk 테이블에 유사도·순위 기록
        │
        ▼
LLM 호출 (선택된 provider)
  ┌─────────────────────────────────────────────────────────┐
  │  한국어 답변 지시 프롬프트                               │
  │  "반드시 한국어로 답변하세요"                            │
  │  + 마크다운 형식 출력 지시                               │
  │  + 출처 기반 답변 원칙                                   │
  └─────────────────────────────────────────────────────────┘
        │
        ▼
응답 반환 + DB 저장 (RagQuery)
  └─ query, response, sources(JSONB), document_ids(JSONB),
     model_used, latency_ms, input_tokens, output_tokens,
     status (success | rate_limit | not_found | bad_request | error)
        │
        ▼
RAG 품질 자동 평가 (RagEvaluation 저장)
  └─ faithfulness · answer_relevancy · context_precision
     (불용어 제거 + 한국어 어근 매칭 기반)
```

### 2-3. AI 문제 생성 파이프라인

```
선택된 문서 ID 목록
        │
        ▼
FAISS Retrieval (k=5, "주요 개념과 핵심 내용")
        │
        ▼
LLM → 객관식 5문제 JSON 생성
  └─ 정규식 추출 + ast.literal_eval 이중 파싱 (마크다운 펜스 제거)
  └─ 문제 · 선택지 · 정답 · 힌트 포함
        │
        ▼
Faithfulness 자동 계산 (_compute_quiz_faithfulness)
  └─ 문제+정답 핵심 키워드가 출처 청크에 포함된 비율 (0.0~1.0)
  └─ 불용어 제거(stopwords_ko.txt) + 한국어 어근 매칭 적용
        │
        ▼
QuizQuestion 저장
  └─ question, choices(JSON), correct_answer, hint, faithfulness_score, chunk_id
```

### 2-4. 사용자 격리 (Security)

- 모든 문서·질문·벡터 검색에 `user_id` 필터 적용
- FAISS callable filter로 다중 문서 선택 시 정확한 소유권 검증
- 소프트 삭제(`is_deleted`) 패턴 — 문서·사용자 모두 적용
- 문서 삭제 시 파일은 `temp/` 폴더로 이동, 벡터 인덱스 재구축
- 캐치올 라우트(`/{path}`)에서 `/api/` 경로 접근 시 404 반환 (HTML 반환 방지)

---

## 3. 페이지 구성

| 경로 | 컴포넌트 | 설명 |
|------|----------|------|
| `/` | Dashboard | 학습 통계(정답률·진도·문서수·질문수), 모델별 사용 현황 그래프, 최근 질문 내역 아코디언, 오답 요약 카드 |
| `/upload` | Upload | 다중 파일 드래그앤드롭 업로드, 업로드 문서 목록·체크박스 선택, 채팅/문제 생성 바로가기, 소프트 삭제 |
| `/chat` | Chat | 다중 문서 체크박스 선택, 모델 선택 드롭다운, 메시지 말풍선 UI, 출처(Sources) 표시, API 오류 팝업(429·404·400) |
| `/questions` | Questions | 문서 다중 선택 + 모델 선택으로 AI 문제 생성, 객관식 풀이·채점, 문제 신뢰도 뱃지(🟢≥80%·🟡50~79%·🔴<50%), 틀린 문제 필터(reviewMode), 문제 삭제 |
| `/review` | WrongAnswersReview | 전체 틀린 문제 재도전, 진행 바, 이전 답 힌트 없이 새로 풀기, 채점 후 정답 강조, 틀린 것만 재도전 |
| `/admin` | AdminDashboard | 전체 통계 요약 카드 5개, 모델별 성능 분석 테이블(순위·지연 색상·min/max), RAG 평가 지표 도넛 차트, 문서별 쿼리 TOP5 미리보기, 사용자 활동량 TOP10, 사용자 등급 관리, 최근 쿼리 미리보기 |
| `/admin/doc-stats` | AdminDocStats | 전체 문서별 쿼리 현황 — 문서명 검색·사용자 드롭다운·정렬 기준/방향·삭제 포함 토글, 페이지네이션 |
| `/admin/logs` | QueryLogs | 전체 RAG 쿼리 로그 — 검색·모델·사용자 필터, 페이지네이션, 토큰·지연 시간 표시, 응답 내용 펼치기 |
| `/admin/users` | AdminUsers | 사용자 목록 검색·페이지네이션, 권한(role) 변경 드롭다운, 강제 탈퇴/복구 버튼, 가입일·탈퇴일 표시 |
| `/admin/ground-truths` | AdminGroundTruths | RAG 평가용 정답 데이터셋 관리 (admin 직접 작성) |
| `/admin/evaluations` | AdminEvaluations | RAG 평가 이력 열람, 기존 평가 데이터 일괄 재계산 버튼 |

### 공통 기능
- **Google OAuth 2.0** 소셜 로그인 (Authlib + Starlette Session)
- **사이드바**: 역할(admin/user)에 따라 관리자 메뉴 조건부 표시
- **회원 탈퇴**: 사이드바 하단 숨김 버튼 → 2단계 경고 모달 → 소프트 삭제 → 자동 로그아웃
- **모델 배지**: 모델별 색상 코드 일관 적용 (전 페이지 통일)
- **마크다운 렌더링**: LLM 응답을 marked.js로 렌더링 (볼드·리스트·코드블록 지원)

---

## 4. API 엔드포인트

### 인증

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/auth` | Google OAuth 로그인 페이지로 리다이렉트 |
| GET | `/login` | Google OAuth 콜백 처리, 세션 저장 |
| GET | `/logout` | 세션 삭제 후 로그아웃 |

### 사용자

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/me` | 현재 로그인 사용자 정보 |
| GET | `/api/available-models` | API 키 존재 여부 기반 사용 가능 모델 목록 |
| GET | `/api/dashboard` | 개인 학습 대시보드 데이터 (통계·오답·최근 쿼리) |
| DELETE | `/api/users/me` | 회원 탈퇴 (소프트 삭제) |

### 문서

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/documents` | 내 문서 목록 (청크 수 포함) |
| POST | `/api/upload` | PDF·TXT 파일 업로드 및 임베딩 처리 |
| DELETE | `/api/documents/{document_id}` | 문서 소프트 삭제 (파일 → temp/ 이동, 벡터 재구축) |
| GET | `/api/documents/{doc_id}/chunks` | 문서의 청크 목록 조회 |

### RAG 챗봇

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/chat` | RAG 질의응답 (document_ids 다중 선택, model 선택) |
| GET | `/api/chat/history` | 내 채팅 이력 조회 |

### 퀴즈

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/questions/generate` | AI 객관식 문제 생성 (faithfulness 자동 계산) |
| GET | `/api/questions` | 문서별 문제 목록 조회 |
| DELETE | `/api/questions` | 문서의 전체 문제 삭제 |
| POST | `/api/questions/attempt` | 문제 풀이 결과 제출 |

### 학습 세션

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/study/sessions` | 내 학습 세션 목록 |

### 관리자 (admin 권한 필요)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/admin/overview` | 대시보드 종합 통계 (JSONB unnest 기반 정확한 집계) |
| GET | `/api/admin/doc-stats` | 문서별 쿼리 현황 상세 (필터·정렬·페이지네이션) |
| GET | `/api/admin/queries` | 전체 RAG 쿼리 로그 |
| GET | `/api/admin/query-models` | 사용된 모델 종류 목록 |
| GET | `/api/admin/users` | 전체 사용자 목록 |
| PUT | `/api/admin/users/{user_id}/role` | 사용자 권한 변경 |
| GET | `/api/admin/evaluations` | RAG 품질 평가 이력 |
| POST | `/api/admin/evaluations/recalculate` | 기존 평가 데이터 일괄 재계산 |
| GET | `/api/admin/ground-truths` | 정답 데이터셋 목록 |
| POST | `/api/admin/ground-truths` | 정답 데이터 등록 |
| DELETE | `/api/admin/ground-truths/{gt_id}` | 정답 데이터 삭제 |

---

## 5. 지원 LLM 모델 비교

| Provider ID | 실제 모델 | 요금 | 특징 |
|-------------|-----------|------|------|
| `openai` | GPT-4o-mini | 유료 | 고품질·안정적, 최다 토큰 |
| `groq` | Llama 3.1 8B Instant | 무료 | ⚡ 초고속 (Groq 전용 추론 칩) |
| `groq-70b` | Llama 3.3 70B Versatile | 무료 | 고품질 대형 오픈소스 |
| `groq-mixtral` | Mixtral 8x7B (32k context) | 무료 | MoE 구조, 긴 컨텍스트 처리 특화 |
| `groq-gemma` | Llama 3.1 8B Instant | 무료 | Groq 추가 슬롯 |
| `huggingface` | Llama 3.1 8B (Groq 경유) | 무료 | HF 키 소유자용 |
| `mistral` | Mistral Small Latest | 무료 티어 | 유럽산 경량 고성능 |

### 임베딩 모델 (고정)

| 용도 | 모델 | 비고 |
|------|------|------|
| 벡터 임베딩 | `paraphrase-multilingual-MiniLM-L12-v2` | sentence-transformers, CPU 실행, 50개 언어 지원 |

> **임베딩을 OpenAI 대신 HuggingFace로 선택한 이유**: 임베딩은 매 청크마다 API를 호출해야 하므로 유료 모델을 쓰면 문서 수가 늘수록 비용이 급증합니다. `paraphrase-multilingual-MiniLM-L12-v2`는 한국어를 포함한 50개 언어를 지원하며, 로컬 CPU에서 실행되므로 비용이 전혀 없고 인터넷 없이도 동작합니다.

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

## 6. DB 스키마

```
users                               ← 플랫폼 사용자 계정
├── id, email (유니크), name
├── role: admin | user
├── is_deleted, deleted_at          ← 소프트 삭제
├── last_login_at
└── created_at (KST)

documents                           ← 사용자가 업로드한 학습 문서
├── id, user_id → users
├── filename, file_path, file_type
├── is_deleted                      ← 소프트 삭제 (파일은 temp/ 이동)
└── uploaded_at

document_chunks                     ← 문서를 분할한 청크 (RAG 검색 단위)
├── id, document_id → documents
├── chunk_index                     ← 문서 내 순서 (0부터)
├── content, token_count
└── embedding_id                    ← FAISS 내부 인덱스 ID

rag_queries                         ← RAG 챗봇 질의·응답 이력
├── id, user_id → users
├── document_id                     ← 대표 문서 (구버전·단일 선택용)
├── query, response
├── sources (JSONB)                 ← 참조 파일명 배열 ["파일A.pdf", ...]
├── document_ids (JSONB)            ← 선택된 문서 ID 배열 [1, 2, 3]
├── model_used, latency_ms
├── input_tokens, output_tokens
├── status: success | rate_limit | not_found | bad_request | error
└── queried_at

rag_retrieved_chunks                ← RAG 검색된 청크 이력
├── query_id → rag_queries
├── chunk_id → document_chunks
├── similarity_score (0.0~1.0)
└── rank                            ← 1 = 가장 유사

rag_evaluations                     ← RAG 응답 품질 자동 평가
├── query_id → rag_queries (유니크)
├── faithfulness                    ← 답변이 컨텍스트에 충실한가 (0~1)
├── answer_relevancy                ← 질문·답변 키워드 일치도 (0~1)
├── context_precision               ← 검색 청크 중 유용한 청크 비율 (0~1)
├── context_recall                  ← 정답 대비 검색된 청크 비율 (ground truth 필요)
└── evaluated_at

rag_ground_truths                   ← RAG 평가용 정답 데이터 (admin 작성)
├── document_id → documents
├── created_by → users
├── question, ideal_answer
└── created_at

quiz_questions                      ← LLM이 자동 생성한 객관식 문제
├── id, document_id → documents
├── chunk_id → document_chunks      ← 출제 근거 청크
├── question
├── choices (JSONB)                 ← ["① ...", "② ...", ...]
├── correct_answer, hint
├── faithfulness_score              ← 문제+정답 키워드의 출처 포함 비율 (0~1)
└── created_at

quiz_attempts                       ← 사용자 퀴즈 풀이 이력
├── id, user_id → users
├── question_id → quiz_questions
├── user_answer, is_correct
└── attempted_at

study_sessions                      ← 문서 학습 세션 추적
├── id, user_id → users
├── document_id → documents
├── started_at, last_accessed_at

agent_sessions / agent_steps        ← AI 에이전트 실행 로그 (확장 예정)

```

> 모든 테이블과 컬럼에 `COMMENT ON TABLE/COLUMN` 메타데이터가 등록되어 있습니다.  
> pgAdmin·DBeaver에서 테이블에 마우스를 올리거나 `obj_description()` 함수로 확인할 수 있습니다.

---

## 7. RAG 품질 평가 시스템

### 7-1. 평가 지표 개요

| 지표 | 계산 방식 | 설명 |
|------|-----------|------|
| **충실도** (Faithfulness) | 답변 키워드 ∩ 컨텍스트 키워드 / 답변 키워드 수 | 답변이 검색된 문서 내용에 근거하는가 |
| **답변 연관성** (Answer Relevancy) | 질문 키워드 ∩ 답변 키워드 / 질문 키워드 수 | 답변이 질문에 관련 있는가 |
| **컨텍스트 정밀도** (Context Precision) | 유용한 청크 수 / 검색된 전체 청크 수 | 검색된 청크 중 실제로 쓰인 비율 |
| **컨텍스트 재현율** (Context Recall) | 정답 키워드 ∩ 컨텍스트 / 정답 키워드 수 | 정답 데이터 기준 — ground truth 필요 |

### 7-2. 한국어 평가의 특수성과 해결 방법

한국어는 조사·어미가 단어에 붙어 있어, 단순 단어 매칭 시 "지도학습이란" ≠ "지도학습" 처럼 오매칭이 발생합니다.

**문제**: 초기 구현에서는 단순 교집합(set intersection)을 사용했는데, 조사·어미가 붙은 형태가 답변에는 없는 원형과 매칭되지 않아 연관성·정밀도가 0%로 산출됨.

**해결책**: 두 단계 처리

```
1단계: 불용어 제거 (backend/stopwords_ko.txt, 600개+ 한국어 불용어)
  └─ 조사(을/를/에/의), 접속사(그리고/그러나), 감탄사(아/휴) 등 제거
  └─ 서버 시작 시 frozenset으로 캐싱 → 반복 호출 비용 0

2단계: 한국어 어근 매칭 (_stem_match)
  └─ 완전 일치 확인 → 없으면 어미 1~4글자씩 제거하며 부분 포함 검사
  └─ "지도학습이란" → "지도학습이라" → "지도학습이" → "지도학습" ✓
  └─ 최소 어근 길이 2글자 보장 (단음절 노이즈 방지)
```

### 7-3. Quiz Faithfulness 점수

AI가 생성한 문제의 품질을 자동으로 측정합니다.

- **의미**: 문제+정답의 핵심 키워드가 출처 청크에 얼마나 포함됐는가 (0.0~1.0)
- **목적**: LLM이 컨텍스트 밖의 내용으로 문제를 만들었는지 검출
- **표시**: 문제 목록에서 🟢≥80% / 🟡50~79% / 🔴<50% 뱃지로 표시

### 7-4. 평가 재계산

기존에 저장된 평가 데이터(0%로 잘못 계산된 이력 포함)를 관리자 페이지에서 일괄 재계산할 수 있습니다.
(`POST /api/admin/evaluations/recalculate`)

---

## 8. 관측성 인프라 (Observability)

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
| Tempo | `grafana/tempo:2.4.2` | 3200, 4317, 4318 | 분산 트레이싱 |
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

> **Loki·Tempo를 추가한 이유**: Prometheus만으로는 "어떤 요청에서 에러가 났는지", "해당 요청의 로그가 무엇인지"를 연결하기 어렵습니다. Loki는 로그를, Tempo는 분산 추적을 담당해 Grafana에서 메트릭 → 로그 → 트레이스를 하나의 화면에서 drill-down할 수 있습니다.

---

## 9. 기술 스택 · 주요 패키지

### Backend

| 분류 | 패키지 | 역할 | 선택 이유 |
|------|--------|------|-----------|
| 웹 프레임워크 | `fastapi`, `uvicorn` | ASGI 서버 | 자동 OpenAPI 문서, 타입 힌트 기반 검증, 비동기 지원 |
| ORM / DB | `sqlalchemy`, `psycopg2-binary` | PostgreSQL 연동 | ORM과 raw SQL(`text()`)을 동시에 사용 가능 |
| 인증 | `authlib` | Google OAuth 2.0 | Starlette 연동 최적화, PKCE 지원 |
| 세션 | `starlette` SessionMiddleware | 서버 사이드 세션 | JWT 없이 간단하게 로그인 상태 유지 |
| RAG 프레임워크 | `langchain`, `langchain-community` | 체인·로더·스플리터 | LLM 추상화 레이어, 다양한 provider 통일 인터페이스 |
| 임베딩 | `langchain-huggingface`, `sentence-transformers` | 다국어 MiniLM (CPU) | API 비용 없음, 한국어 포함 50개 언어, CPU만으로 충분한 성능 |
| 벡터스토어 | `faiss-cpu` | 인메모리 유사도 검색 | callable filter로 user_id/document_id 기반 격리 가능, 별도 벡터 DB 서버 불필요 |
| 문서 로더 | `pymupdf`, `pypdf` | PDF 텍스트 추출 | PyMuPDF가 한글 인코딩에 더 안정적, 실패 시 PyPDF로 fallback |
| LLM — OpenAI | `langchain-openai` | GPT-4o-mini | 기준 모델 (최고 품질) |
| LLM — Groq | `langchain-groq` | Llama 3.1/3.3, Kimi K2 | 무료 + 빠른 추론 속도, 성능 비교용 |
| LLM — Mistral | `langchain-mistralai` | Mistral Small | 유럽산 경량 모델, 무료 티어 |
| 메트릭 | `prometheus-fastapi-instrumentator` | HTTP 메트릭 자동 수집 | FastAPI 미들웨어로 코드 수정 없이 수집 |
| 트레이싱 | `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` | Tempo 연동 | 표준 OTLP 프로토콜, vendor-agnostic |
| 자동 계측 | `opentelemetry-instrumentation-fastapi` | FastAPI span 자동 생성 | 코드 한 줄로 모든 HTTP 요청 추적 |
| 로그 | `python-logging-loki` | Loki 전송 | 기존 Python logging 핸들러 방식으로 연동, 기존 로그 코드 수정 불필요 |
| 유틸 | `python-dotenv`, `httpx` | 환경변수, HTTP 클라이언트 | Ollama 상태 확인(ping) 등 |

### Frontend (CDN, 빌드 단계 없음)

| 라이브러리 | 역할 | 선택 이유 |
|------------|------|-----------|
| Vue 3 (Composition API) | SPA 프레임워크 | `ref`·`computed`·`watch` 기반 반응형, 빌드 없이 CDN으로 사용 가능 |
| Vue Router 4 | 클라이언트 라우팅 | Hash 기반(`#/path`) — 서버 사이드 라우팅 설정 없이 SPA 동작 |
| Axios | REST API 통신 | 인터셉터, 자동 JSON 파싱, 에러 상태코드 자동 throw |
| TailwindCSS (CDN) | 유틸리티 퍼스트 스타일링 | 빠른 반응형 UI 구성, 별도 CSS 파일 불필요 |
| marked.js (CDN) | 마크다운 렌더링 | LLM 응답의 볼드·리스트·코드블록 표시 |

> **빌드 없는 프론트엔드를 선택한 이유**: FastAPI가 `frontend/` 폴더를 정적 파일로 서빙하는 구조이므로, 별도 Node.js 빌드 파이프라인 없이 `app.js` 파일 수정만으로 즉시 반영됩니다. 개발 속도와 배포 단순성을 최우선으로 고려했습니다.

---

## 10. 성능 개선 이력 · 트러블슈팅

### 10-1. 한글 PDF 인코딩 깨짐 문제

**증상**: 한글이 많은 PDF 업로드 시 청크 내용이 `???` 또는 의미 없는 문자열로 저장됨.

**원인**: `PyPDFLoader`는 일부 한글 폰트(특히 임베딩되지 않은 한글 폰트)를 제대로 파싱하지 못함.

**해결**: `PyMuPDFLoader` (pymupdf 패키지)를 우선 시도하고 실패 시 `PyPDFLoader`로 fallback하는 이중 로더 구조 적용.

```python
try:
    loader = PyMuPDFLoader(file_path)
    docs = loader.load()
except Exception:
    loader = PyPDFLoader(file_path)
    docs = loader.load()
```

---

### 10-2. RAG 평가 지표가 전부 0%로 나오던 문제

**증상**: 평가 이력 페이지에서 `answer_relevancy`(답변 연관성)와 `context_precision`(컨텍스트 정밀도)가 항상 0%로 표시됨.

**원인**: 단순 Python `set` 교집합 방식을 사용했는데, 한국어는 조사·어미가 붙어 "지도학습이란"과 "지도학습"이 다른 단어로 처리됨.

```python
# 문제가 된 기존 코드
q_words = set(query.split())
a_words = set(response.split())
score = len(q_words & a_words) / len(q_words)  # 교집합이 항상 0
```

**해결**: 불용어 제거 + 한국어 어근 매칭 2단계 방식으로 교체.

1. `backend/stopwords_ko.txt` (600개+ 불용어) 로드 → `frozenset` 캐싱 (서버 시작 시 1회)
2. `_stem_match()` 함수: 어미 1~4글자씩 순차 제거하며 원형 포함 여부 확인

**결과**: 0%이던 연관성·정밀도 지표가 실제 의미 있는 수치(40~80% 수준)로 산출됨.

---

### 10-3. 다중 문서 선택 시 쿼리 집계 오류

**증상**: 관리자 대시보드의 "문서별 쿼리 현황"에서 문서 A·B·C를 동시에 선택해서 한 질문을 해도, 세 문서 중 `document_id`(대표 문서 1개)에만 카운트되고 나머지 두 문서에는 집계되지 않음.

**원인**: `rag_queries` 테이블의 `document_id`(단일 정수)만 참조하던 기존 SQL. 다중 선택은 `document_ids` JSONB 배열에 저장됨.

**해결**: PostgreSQL의 JSONB unnest 기능으로 배열을 행으로 분해해 각 문서에 개별 카운트.

```sql
WITH query_docs AS (
    -- document_ids 배열이 있는 신규 쿼리: 각 문서 ID에 1씩 카운트
    SELECT CAST(elem AS INTEGER) AS doc_id, rq.id AS query_id
    FROM rag_queries rq
    JOIN LATERAL jsonb_array_elements_text(rq.document_ids) AS elem ON true
    WHERE rq.document_ids IS NOT NULL
      AND jsonb_typeof(rq.document_ids) = 'array'
      AND jsonb_array_length(rq.document_ids) > 0

    UNION ALL

    -- 구버전 쿼리: document_id(단일 값) 사용
    SELECT rq.document_id AS doc_id, rq.id AS query_id
    FROM rag_queries rq
    WHERE rq.document_ids IS NULL
       OR jsonb_typeof(rq.document_ids) != 'array'
       OR jsonb_array_length(rq.document_ids) = 0
)
```

---


## 11. 설치 · 실행

### 사전 요구사항

- Python 3.10+
- PostgreSQL 실행 중 (기본: `localhost:5432`)
- (선택) Docker — 관측성 스택 실행 시

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

프로젝트 루트에 `.env` 파일 생성 → [환경 변수 섹션](#12-환경-변수) 참고

### 3. 서버 실행

```bash
python main.py
```

서버 시작 시 자동 처리:

- PostgreSQL `study_platform` DB 없으면 자동 생성
- `Base.metadata.create_all()` — 테이블 자동 생성
- 멱등 마이그레이션 (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)
- 테이블·컬럼 코멘트 자동 등록 (`COMMENT ON TABLE/COLUMN`)
- HuggingFace 임베딩 모델 로컬 캐시 로드 (최초 실행 시 다운로드)
- 한국어 불용어 사전 로드 (`backend/stopwords_ko.txt` → frozenset 캐싱)
- 기존 문서 청크 기반 FAISS 인덱스 자동 재구축

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

## 12. 환경 변수

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
> Loki·Tempo 미실행 시 관련 에러는 무시되고 서버는 정상 동작합니다.
