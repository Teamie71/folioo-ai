# Folioo AI

Folioo AI는 사용자의 프로젝트 경험을 인터뷰로 구조화하고, 포트폴리오 문장 생성과 첨삭 보조를 제공하는 AI 백엔드입니다. FastAPI API 서버와 LangGraph 기반 인터뷰 에이전트를 중심으로 동작하며, 메인 NestJS 백엔드와 연동됩니다.

## 주요 기능

- 인터뷰 세션 생성, 상태 조회, 단계별 대화 진행
- SSE 기반 AI 응답 토큰 스트리밍
- PDF/이미지 업로드를 포함한 인터뷰 답변 처리
- 인터뷰 완료 후 포트폴리오 결과 생성
- 자기소개서/포트폴리오 첨삭 RAG 및 기업 분석 보조
- PostgreSQL checkpointer 기반 LangGraph 세션 상태 저장

## 기술 스택

- Python 3.12+
- FastAPI, Uvicorn
- LangGraph, LangChain
- OpenRouter 호환 ChatOpenAI 클라이언트
- PostgreSQL checkpointer
- uv, Ruff, pytest

## 시작하기

```bash
uv sync --dev
cp .env.example .env
python main.py
```

기본 개발 서버는 `127.0.0.1:8000`에서 실행됩니다. `PORT`, `UVICORN_HOST`, `UVICORN_RELOAD` 환경변수로 실행 옵션을 조정할 수 있습니다.

## 개발 명령어

```bash
ruff check .
ruff format .
pytest
langgraph dev
```

## 주요 환경변수

| 변수 | 설명 |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter LLM API 키 |
| `LLM_MODEL_NAME` | 기본 LLM 모델명 |
| `PDF_EXTRACTION_MODEL_NAME` | PDF 추출용 LLM 모델명 |
| `FILE_PROCESSOR_MODEL_NAME` | 업로드 파일 처리용 LLM 모델명 |
| `DATABASE_URL` | 기본 PostgreSQL 연결 문자열 |
| `CHECKPOINT_DATABASE_URL` | LangGraph checkpointer 전용 PostgreSQL 연결 문자열. 미설정 시 `DATABASE_URL` 재사용 |
| `AI_SERVICE_API_KEY` | 메인 백엔드가 AI 서버 호출 시 사용하는 API 키 |
| `MAIN_BACKEND_URL` | 메인 NestJS 백엔드 URL |
| `MAIN_BACKEND_API_KEY` | AI 서버가 메인 백엔드 호출 시 사용하는 API 키 |
| `ALLOWED_ORIGINS` | CORS 허용 origin 목록 |
| `TAVILY_API_KEY` | 웹 검색용 Tavily API 키 |

## API 문서

서버 실행 후 다음 경로에서 OpenAPI 문서를 확인할 수 있습니다.

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- Health check: `/health`

## 아키텍처 문서

- `docs/architecture/sse-streaming.md`: 인터뷰 SSE 스트리밍 이벤트 프로토콜
- `docs/architecture/worker-runtime.md`: 시각화 워커 실행 환경과 큐 핸들러 패턴
- `docs/architecture/worker-spec.md`: 시각화 워커 사양과 인프라 구조
- `docs/architecture/template-system.md`: PPTX 템플릿 시스템
- `docs/architecture/ooxml-editing.md`: OOXML 편집 방식
- `docs/architecture/qa-and-guardrails.md`: QA 및 가드레일
- `docs/adr/`: 주요 아키텍처 의사결정 기록
