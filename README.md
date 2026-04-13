# RAG Study Platform (AI 기반 학습 플랫폼)

본 프로젝트는 다양한 AI 모델(OpenAI, Groq, HuggingFace 등)을 활용하여 사용자가 업로드한 문서(PDF, TXT)를 기반으로 채팅 학습 및 문제 생성을 제공하는 RAG(Retrieval-Augmented Generation) 시스템입니다. 또한 시스템의 성능 측정을 위해 Grafana 스택 기반의 관측성 인프라를 포함하고 있습니다.

## 🚀 주요 기능

1.  **멀티 모델 RAG (Multi-model RAG)**
    *   **OpenAI (GPT-4o-mini):** 고성능 안정적 답변
    *   **Groq (Llama 3.1):** 초고속 응답 속도 (무료 API 활용)
    *   **HuggingFace (Local):** 로컬 CPU 기반의 완전 무료 임베딩 및 처리 지원
2.  **통합 벡터 DB 구축**
    *   문서 업로드 시 `HuggingFace` 다국어 임베딩 모델을 사용하여 하나의 벡터 저장소(`FAISS`)를 구축합니다.
    *   한 번의 업로드로 모든 LLM 모델을 자유롭게 변경하며 테스트할 수 있습니다.
3.  **지능형 학습 도구**
    *   **챗봇 학습:** 문서 내용을 바탕으로 한 질문 답변 및 대화 내역 저장.
    *   **문제은행:** 문서 내용을 기반으로 AI가 자동 생성한 객관식 문제 풀이.
    *   **복습 모드:** 틀린 문제만 따로 모아볼 수 있는 필터링 기능.
4.  **강력한 모니터링 (Observability)**
    *   **Prometheus:** 모델별 지연 시간(Latency) 및 호출 횟수 수집.
    *   **Grafana:** 모델별 성능 비교 대시보드 자동 제공.
    *   **Tempo & Loki:** 트레이싱 및 로그 통합 관리.

---

## 🛠 시스템 아키텍처 및 파이프라인

### 1. RAG 파이프라인 (Data Flow)
1.  **Ingestion:** 사용자가 파일을 업로드하면 `RecursiveCharacterTextSplitter`를 통해 텍스트를 청크(Chunk) 단위로 분할합니다.
2.  **Embedding:** 분할된 텍스트를 `sentence-transformers` 모델을 사용하여 벡터화합니다.
3.  **Storage:** 벡터화된 데이터를 메모리 기반 `FAISS` 인덱스에 저장합니다. (서버 재시작 시 DB 기반 자동 복구 지원)
4.  **Retrieval:** 사용자의 질문이 들어오면 유사도 검색을 통해 관련 문서 조각들을 찾아냅니다.
5.  **Generation:** 검색된 컨텍스트와 질문을 결합하여 선택된 LLM(OpenAI, Groq 등)에 전달하고 최종 답변을 생성합니다.

### 2. 모니터링 파이프라인
*   `main.py` (FastAPI) -> `Prometheus` (Metrics) -> `Grafana` (Visualization)
*   `main.py` -> `OpenTelemetry` -> `Tempo` (Tracing)

---

## 📊 Grafana 대시보드 구성 (RAG Model Performance)

모니터링 스택 실행 후 `http://localhost:3001` 에서 **RAG Model Performance** 대시보드가 자동으로 프로비저닝됩니다.

### 대시보드 레이아웃

```
┌──────────────────────────────┬──────────────────────────────┐
│  모델별 호출 횟수 (calls/sec) │  모델별 평균 응답 시간 (Avg) │  ← 1행
├──────────────────────────────┤                              │
│  모델별 누적 호출 수 (숫자)   │                              │  ← 2행
├──────────────────────────────┬──────────────────────────────┤
│  FastAPI HTTP 요청 처리율    │  FastAPI HTTP 요청 지연      │  ← 3행
└──────────────────────────────┴──────────────────────────────┘
```

### 패널별 설명

#### 1. 모델별 호출 횟수 (calls/sec)
- **타입:** 시계열 그래프
- **PromQL:** `rate(rag_model_latency_seconds_count[5m])`
- **의미:** 최근 5분 기준 초당 API 호출 수를 모델(provider)별로 보여줍니다.
- **활용:** 어느 모델이 가장 많이 사용되고 있는지 트렌드 파악에 사용합니다.
- **단위:** reqps (requests per second)

#### 2. 모델별 평균 응답 시간 (Avg Latency)
- **타입:** 시계열 그래프
- **PromQL:** `rate(rag_model_latency_seconds_sum[5m]) / rate(rag_model_latency_seconds_count[5m])`
- **의미:** 최근 5분 동안 각 모델이 응답하는 데 걸린 평균 시간입니다.
- **활용:** OpenAI, Groq, Gemini 등 모델 간 속도를 직접 비교할 때 사용합니다.
- **단위:** 초(s) — 예: 2.3s = 평균 2.3초 소요
- **참고:** `sum / count` 형태의 표준 Histogram 패턴을 사용하며, 성공한 요청에 대해서만 기록됩니다.

#### 3. 모델별 누적 호출 수
- **타입:** Stat 패널 (숫자 카드)
- **PromQL:** `sum(rag_model_latency_seconds_count) by (provider)`
- **의미:** 서버 시작 이후 각 모델의 총 누적 호출 횟수입니다.
- **활용:** 시계열이 아닌 절대값으로 모델 사용 비중을 파악합니다.

#### 4. FastAPI HTTP 요청 처리율
- **타입:** 시계열 그래프
- **PromQL:** `rate(http_requests_total[5m])`
- **의미:** `prometheus_fastapi_instrumentator`가 자동으로 수집하는 HTTP 요청률입니다. 메서드(GET/POST), 엔드포인트, 상태코드별로 분리됩니다.
- **활용:** 어느 API 엔드포인트에 트래픽이 몰리는지 파악합니다.

#### 5. FastAPI HTTP 요청 지연 (Avg)
- **타입:** 시계열 그래프
- **PromQL:** `rate(http_request_duration_seconds_sum[5m]) / rate(http_request_duration_seconds_count[5m])`
- **의미:** 엔드포인트별 HTTP 요청의 평균 처리 시간입니다.
- **활용:** 특정 API가 갑자기 느려지는 현상을 빠르게 탐지합니다.

### 메트릭 수집 구조

```
FastAPI 요청
    │
    ├─ prometheus_fastapi_instrumentator (자동)
    │       http_requests_total            ← 패널 4 사용
    │       http_request_duration_seconds  ← 패널 5 사용
    │
    └─ 커스텀 메트릭 (main.py에서 직접 기록)
            rag_model_latency_seconds      ← 패널 1,2,3 사용
              · label: provider (openai / groq / gemini 등)
              · 성공 응답 시에만 기록
```

### 대시보드 설정
- **자동 새로고침:** 5초
- **기본 조회 범위:** 최근 1시간
- **Provisioning 파일:** `grafana_dashboard.json`

---

## 📋 기술 사양

### Backend
*   **Python:** 3.10+
*   **Framework:** FastAPI
*   **Database:** PostgreSQL (SQLAlchemy ORM)
*   **RAG:** LangChain, FAISS (Vector Store)
*   **Auth:** Google OAuth 2.0 (Authlib)

### Frontend
*   **Framework:** Vue.js 3 (Composition API)
*   **Router:** Vue Router 4
*   **Styling:** TailwindCSS

### Observability
*   Grafana, Prometheus, Loki, Tempo (Docker Compose 구성)

---

## 📦 설치 및 실행 방법

### 1. 환경 변수 설정
프로젝트 루트에 `.env` 파일을 생성하고 아래 키를 입력합니다.
```env
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza... (Gemini/Google Search용)
GROQ_API_KEY=gsk_...
HUGGINGFACE_API_KEY=hf_...
GOOGLE_CLIENT_ID=... (Google Login용)
GOOGLE_CLIENT_SECRET=... (Google Login용)
SECRET_KEY=any-secret-key
DATABASE_URL=postgresql://user:pass@localhost:5432/db_name
```

### 2. 패키지 설치
```bash
pip install -r requirements.txt
```

### 3. 서버 실행
```bash
python main.py
```

### 4. 모니터링 시스템 실행 (Docker 필수)
```bash
docker-compose -f docker-compose.monitoring.yml up -d
```
*   Grafana: `http://localhost:3001` (자동 생성된 "RAG Model Performance" 대시보드 확인)

---

## 📄 사용된 주요 패키지 (requirements.txt 주요 목록)
*   `langchain`, `langchain-openai`, `langchain-groq`, `langchain-google-genai`
*   `faiss-cpu`, `sentence-transformers`, `pypdf`
*   `fastapi`, `uvicorn`, `sqlalchemy`, `psycopg2-binary`
*   `prometheus-fastapi-instrumentator`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`
