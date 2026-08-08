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

## 의존성

- 3.04 (세션·요청 Repository) — request claim·상태 저장
- 3.07 (첨부 파일 저장) — stream 을 열기 전 업로드 검증
- 3.08 (LangGraph 상태) — graph 호출 인터페이스
- 3.09 (티켓 검증) — 미들웨어 적용

## 사전 준비

- [ ] API 명세 6절 SSE 이벤트 예시 전수 확인
- [ ] 기존 `common/sse/` 유틸과 인터뷰 챗 SSE 구현 패턴 확인

## 구현 체크리스트

- [ ] `app/api/v1/experience_map.py` — 엔드포인트 5종
  - [ ] `POST /api/v1/experience-map/sessions`
  - [ ] `GET  /api/v1/experience-map/sessions/{session_id}/state`
  - [ ] `POST /api/v1/experience-map/sessions/{session_id}/chat/stream`
  - [ ] `POST /api/v1/experience-map/sessions/{session_id}/retry/stream`
  - [ ] `GET  /api/v1/experience-map/sessions/{session_id}/requests/{request_id}`
- [ ] `app/api/v1/__init__.py` 에 router 등록
- [ ] `langgraph.json` 에 경험정리 graph 등록
- [ ] 10초 heartbeat
- [ ] stream 시작 전 JSON 오류 / 시작 후 SSE 오류
- [ ] 이벤트 `processing_started`·`node_status`·`commit_result`·`message_complete`·`suggestion_ready`·`processing_complete`·`error`·`ping`
- [ ] **`EXPERIENCE_MAP_ENABLED` 도입, 기본값 `false`**
- [ ] mock graph 로 전체 API 계약 테스트

## Definition of Done

- [ ] mock graph 로 전체 API 계약 테스트가 통과한다
- [ ] 잘못된 업로드는 stream 을 열기 전에 거부된다
- [ ] 브라우저에서 직접 SSE 연결이 확인된다
- [ ] 같은 running request 의 중복 stream 은 409, completed request 는 저장 이벤트를 재전송한다
- [ ] flag 가 `false` 면 라우트가 등록되지 않는다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **L 크기다.** 리뷰가 무거우면 `엔드포인트 5종` → `SSE 이벤트 스트림` 두 PR 로 쪼갤 수 있다. 다만 mock graph 계약 테스트가 두 PR 에 걸쳐 반쪽이 되므로, 리뷰어를 붙일 수 있으면 한 PR 로 간다.
- feature flag 를 여기서 도입하고 3.23 에서 뒤집는다. 그 전까지 미완성 기능이 노출되지 않는다.
