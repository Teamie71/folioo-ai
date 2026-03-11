# Folioo AI Gemini 리뷰 가이드

## 1) 응답 언어

- 리뷰 코멘트, PR 요약, 질의응답은 모두 한국어로 작성한다.
- 코드 식별자(함수/클래스/변수명), 에러 코드, 이벤트명, 라이브러리명은 원문(영문) 그대로 유지한다.

## 2) 리뷰 우선순위

- 정확성 > 회귀 위험 > 보안 > 성능 > 유지보수성 > 스타일 순서로 검토한다.
- 요청 범위를 벗어난 대규모 리팩터링은 강요하지 않고, 필요 시 "추가 제안"으로 분리한다.

## 3) 저장소 컨텍스트

- Python 3.12+, FastAPI, LangGraph, LangChain, asyncpg, Tavily 기반 백엔드.
- 주요 구조:
    - `app/api/v1`: API 엔드포인트
    - `app/schemas`: Pydantic API 스키마
    - `features/interview`: LangGraph 인터뷰 에이전트
    - `features/portfolio`: 포트폴리오 생성
    - `features/correction`: 첨삭 + RAG
    - `common/*`: 공통 유틸 (LLM, DB, SSE, checkpointer)
    - `tests/*`: 기능별 미러 구조 테스트

## 4) 코드 스타일 규칙

- Ruff 기준을 따른다.
    - line length: 100
    - quote style: double quotes
    - import 정렬: isort
- import 순서:
    1. 표준 라이브러리
    2. 서드파티
    3. 퍼스트파티(`app`, `features`, `common`)
- 타입 힌트는 Python 3.12 문법(`list[str]`, `dict[str, int]`, `str | None`)을 우선한다.
- 독스트링은 Google 스타일 + 한국어 설명을 유지한다.
- `__init__.py`의 명시적 export(`__all__`) 패턴을 존중한다.

## 5) FastAPI/API 검토 포인트

- 상태코드/에러 매핑(400/404/409/500) 계약을 임의로 깨지 않았는지 확인한다.
- UUID path 파라미터 검증 패턴(`_validate_*_id`) 유지 여부를 확인한다.
- 사용자 노출 메시지는 한국어를 우선한다.
- 인증 로직(`X-API-Key`, `secrets.compare_digest`) 약화 변경은 보안 이슈로 분류한다.

## 6) 비동기/성능 검토 포인트

- 이벤트 루프를 블로킹하는 동기 작업 유입 여부를 확인한다.
- 동기 LLM/CPU 작업은 `asyncio.to_thread` 패턴을 우선 권장한다.
- 백그라운드 작업(`BackgroundTasks`)과 상태 전이(`generating`, `failed` 등) 정합성을 확인한다.

## 7) 인터뷰 에이전트(LangGraph) 검토 포인트

- `InterviewState` 핵심 키와 1~4단계 전이 불변식을 유지하는지 확인한다.
- `stage_progress` 필드(`fixed_q_used`, `generated_q_used`, `is_complete`) 계산의 일관성을 확인한다.
- checkpointer의 `thread_id=session_id` 기반 복원 흐름이 깨지지 않는지 확인한다.
- SSE 이벤트 계약을 보존하는지 확인한다:
    - `content_block_delta`
    - `retriever_status`
    - `retriever_result`
    - `message_complete`
    - `error`
    - `ping`

## 8) DB/보안 검토 포인트

- `asyncpg` SQL은 파라미터 바인딩(`$1`, `$2`, ...)을 사용해야 한다.
- SQL 문자열 보간으로 사용자 입력을 직접 삽입하면 안 된다.
- 민감정보(`OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `DATABASE_URL`) 노출 가능성을 확인한다.
- correction/portfolio 상태 전이 규칙 위반 가능성을 중점 검토한다.

## 9) 테스트 기준

- 변경된 동작에 대해 `pytest` 테스트가 추가/수정되었는지 확인한다.
- 기존 테스트 패턴(monkeypatch, Dummy 객체, fixture, 상태 전이 검증)을 따른다.
- 비동기 로직은 `pytest.mark.asyncio` 또는 `pytest.mark.anyio`로 검증한다.
- 권장 검증 명령:
    - `ruff check .`
    - `ruff format .`
    - `pytest`

## 10) 리뷰 코멘트 작성 형식

- 코멘트는 "문제 → 근거 → 수정 제안" 순서로 간결하게 작성한다.
- 차단 이슈(필수 수정)와 권장 이슈(선택 개선)를 구분한다.
- 가능하면 짧은 수정 방향 또는 예시를 함께 제시한다.
