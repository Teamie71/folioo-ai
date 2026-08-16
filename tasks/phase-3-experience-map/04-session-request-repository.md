---
id: "3.04"
phase: 3
title: "세션·요청 Repository 와 lease 관리"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.01", "3.02"]
blocks: ["3.10"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.04 — 세션·요청 Repository 와 lease 관리

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 7-3, 9절 4번
> PR: EM-04 · 브랜치 `feat/{issue}-experience-map-session-repo`
> GitHub Issue: [#305](https://github.com/Teamie71/folioo-ai/issues/305)

## 의존성

- 3.01 (DB 연결 분리) — 경험 맵 DB pool 을 사용한다.
- 3.02 (스키마·오류 모델)
- **외부-A**: 메인 DB migration (`ai_experience_session`, `ai_experience_request`, 세션당 running 1개 partial unique index). 미완료 시 동일 스키마 픽스처로 선행 개발한다.

## 사전 준비

- [x] 메인 DB migration 의 `ai_experience_*` 테이블 정의 확보
- [x] AI 서버 DB 계정 권한 확인 (`ai_experience_*` 만 쓰기)

## 구현 체크리스트

- [x] `repository.py` — `get_or_create_session(user_id)`, `get_session(session_id)`
- [x] `claim_request(session_id, request_id, request_hash, input_meta)`
- [x] `renew_request_lease(...)` — 30초 주기 갱신 task
- [x] `get_request(...)`, `mark_request_failed(...)`, `mark_request_completed(...)`
- [x] 만료된 running 요청 정리
- [x] 30일이 지난 완료 요청 정리
- [x] 새 요청 시작 시 이전 failed 요청의 사용자 재시도 비활성화

## Definition of Done

- [x] 여러 worker 에서 같은 세션을 동시 실행해도 하나만 성공한다
- [x] 프로세스 중단 뒤 lease 만료로 복구된다
- [x] 다른 사용자 세션·요청 접근이 차단된다
- [x] 같은 request ID·같은 hash 는 저장 상태를 반환하고, 다른 hash 는 충돌이다
- [x] lease 갱신 task 가 실패하면 실행을 중단하고 failed 로 저장한다
- [x] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **`ai_experience_request` 가 API 상태의 유일한 기준이다** (7-3). checkpoint status 를 API 상태 판단에 쓰는 코드가 들어오지 않는지 리뷰에서 확인한다.
- 결과: `1084 passed` (신규 26). ruff check·format 통과.
- **테스트를 실제 PostgreSQL 로 돌린다.** 세션당 running 1건과 lease 만료는 asyncpg 를 mock 으로 감싸면 아무것도 검증하지 못한다. DB 가 없으면 skip 하므로 CI 는 영향받지 않는다.
- 동시성은 DB 가 막는다. `claim_request` 는 partial unique index 의 `UniqueViolationError` 를 `SESSION_BUSY` 로 번역한다. 애플리케이션 락을 쓰지 않는다.
- 시각은 전부 DB 의 `now()` 를 쓴다. worker 마다 시계가 다르면 lease 만료 판정이 갈린다.
- **만료 정리와 새 요청 시작은 재시도 자격을 다르게 다룬다.** lease 만료만으로는 `retryable` 이 유지되고(`GET /state` 경로), 새 요청이 시작되면 이전 failed 는 재시도할 수 없다(9절 4번). 둘 다 테스트로 고정했다.
- ⚠️ `expire_stale_running_requests()` 는 3.22 에서 **`GET /commit/{request_id}` 확인을 앞에 넣어야 한다** (명세 4-3). 커밋은 성공했는데 응답만 유실된 요청을 failed 로 만들면 사용자가 같은 내용을 두 번 커밋한다.
