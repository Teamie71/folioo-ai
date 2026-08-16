---
id: "3.10"
phase: 3
title: "경험정리 API·SSE 뼈대와 mock graph 계약 테스트"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.04", "3.07", "3.08", "3.09"]
blocks: ["3.11"]
estimate: "L"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.10 — 경험정리 API·SSE 뼈대와 mock graph 계약 테스트

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 9절 8번
> API: [`docs/architecture/experience-map-api-spec.md`](../../docs/architecture/experience-map-api-spec.md) 6절
> PR: EM-10 · 브랜치 `feat/{issue}-experience-map-api-skeleton`
> GitHub Issue: [#306](https://github.com/Teamie71/folioo-ai/issues/306)

## 의존성

- 3.04 (세션·요청 Repository) — request claim·상태 저장
- 3.07 (첨부 파일 저장) — stream 을 열기 전 업로드 검증
- 3.08 (LangGraph 상태) — graph 호출 인터페이스
- 3.09 (티켓 검증) — 미들웨어 적용

## 사전 준비

- [x] API 명세 6절 SSE 이벤트 예시 전수 확인
- [x] 기존 `common/sse/` 유틸과 인터뷰 챗 SSE 구현 패턴 확인

## 구현 체크리스트

- [x] `app/api/v1/experience_map.py` — 엔드포인트 5종
  - [x] `POST /api/v1/experience-map/sessions`
  - [x] `GET  /api/v1/experience-map/sessions/{session_id}/state`
  - [x] `POST /api/v1/experience-map/sessions/{session_id}/chat/stream`
  - [x] `POST /api/v1/experience-map/sessions/{session_id}/retry/stream`
  - [x] `GET  /api/v1/experience-map/sessions/{session_id}/requests/{request_id}`
- [x] `app/api/v1/__init__.py` 에 router 등록
- [ ] `langgraph.json` 에 경험정리 graph 등록 — **3.17 로 미룸.** `features.experience_map.graph:graph` 가 아직 없어 지금 등록하면 `langgraph dev` 가 인터뷰 그래프까지 못 띄운다
- [x] 10초 heartbeat
- [x] stream 시작 전 JSON 오류 / 시작 후 SSE 오류
- [x] 이벤트 `processing_started`·`node_status`·`commit_result`·`message_complete`·`suggestion_ready`·`processing_complete`·`error`·`ping`
- [x] **`EXPERIENCE_MAP_ENABLED` 도입, 기본값 `false`**
- [x] mock graph 로 전체 API 계약 테스트

## Definition of Done

- [x] mock graph 로 전체 API 계약 테스트가 통과한다
- [x] 잘못된 업로드는 stream 을 열기 전에 거부된다
- [x] 브라우저에서 직접 SSE 연결이 확인된다
- [x] 같은 running request 의 중복 stream 은 409, completed request 는 저장 이벤트를 재전송한다
- [x] flag 가 `false` 면 라우트가 등록되지 않는다
- [x] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **L 크기다.** 리뷰가 무거우면 `엔드포인트 5종` → `SSE 이벤트 스트림` 두 PR 로 쪼갤 수 있다. 다만 mock graph 계약 테스트가 두 PR 에 걸쳐 반쪽이 되므로, 리뷰어를 붙일 수 있으면 한 PR 로 간다.
- feature flag 를 여기서 도입하고 3.23 에서 뒤집는다. 그 전까지 미완성 기능이 노출되지 않는다.
- 결과: `1204 passed` (신규 19). DB 없는 환경에서는 44 skipped. ruff check·format 통과.
- **`MockGraphRunner` 를 기본 실행기로 둔다.** 노드가 하나도 없어도 API 계약과 이벤트 순서를 로컬에서 확인할 수 있어야 한다. 3.17 에서 실제 그래프로 교체한다.
- **실기동으로 전체 흐름을 확인했다.** `POST /sessions` → 티켓 발급 → `chat/stream` SSE 17개 이벤트 → `GET /state` → `GET /requests/{id}` → 만료·위조 티켓 401.
- 테스트는 `TestClient` 가 아니라 `httpx.AsyncClient` 를 쓴다. `TestClient` 는 앱을 별도 이벤트 루프에서 돌려 asyncpg 풀이 다른 루프에 묶인다.
- `sse_starlette` 의 `AppStatus.should_exit_event` 는 클래스 속성이라 테스트마다 초기화해야 한다.
- ⚠️ 3.04 의 `init_repository()` 를 lifespan 에 연결하는 것을 빠뜨렸다가 실기동에서 발견했다. 단위 테스트는 Repository 를 주입하므로 잡히지 않는 종류다.
