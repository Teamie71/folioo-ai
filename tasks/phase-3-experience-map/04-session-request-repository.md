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

- [ ] 메인 DB migration 의 `ai_experience_*` 테이블 정의 확보
- [ ] AI 서버 DB 계정 권한 확인 (`ai_experience_*` 만 쓰기)

## 구현 체크리스트

- [ ] `repository.py` — `get_or_create_session(user_id)`, `get_session(session_id)`
- [ ] `claim_request(session_id, request_id, request_hash, input_meta)`
- [ ] `renew_request_lease(...)` — 30초 주기 갱신 task
- [ ] `get_request(...)`, `mark_request_failed(...)`, `mark_request_completed(...)`
- [ ] 만료된 running 요청 정리
- [ ] 30일이 지난 완료 요청 정리
- [ ] 새 요청 시작 시 이전 failed 요청의 사용자 재시도 비활성화

## Definition of Done

- [ ] 여러 worker 에서 같은 세션을 동시 실행해도 하나만 성공한다
- [ ] 프로세스 중단 뒤 lease 만료로 복구된다
- [ ] 다른 사용자 세션·요청 접근이 차단된다
- [ ] 같은 request ID·같은 hash 는 저장 상태를 반환하고, 다른 hash 는 충돌이다
- [ ] lease 갱신 task 가 실패하면 실행을 중단하고 failed 로 저장한다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **`ai_experience_request` 가 API 상태의 유일한 기준이다** (7-3). checkpoint status 를 API 상태 판단에 쓰는 코드가 들어오지 않는지 리뷰에서 확인한다.
