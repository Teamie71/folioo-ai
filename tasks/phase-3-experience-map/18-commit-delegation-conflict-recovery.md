---
id: "3.18"
phase: 3
title: "커밋 위임과 map version 충돌 복구"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.06", "3.17"]
blocks: ["3.20"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.18 — 커밋 위임과 map version 충돌 복구

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 5-8, 9절 16번
> API: [`docs/architecture/experience-map-api-spec.md`](../../docs/architecture/experience-map-api-spec.md) 4-3
> PR: EM-18 · 브랜치 `feat/{issue}-experience-map-commit`
> GitHub Issue: [#309](https://github.com/Teamie71/folioo-ai/issues/309)

## 의존성

- 3.06 (커밋 클라이언트) — HTTP 계층
- 3.17 (validate·graph 배선) — 재실행 진입점(validate / structure)

## 사전 준비

- [ ] 3.06 이 승격한 `409`·`422` 예외 타입 확인
- [ ] "구조 유지 vs 구조 변경" 판단 기준 정의

## 구현 체크리스트

- [ ] `nodes/commit.py` — alias → 실제 ID 역변환 후 items 전송
- [ ] commit 구간 cancellation 차단 (`asyncio.shield`)
- [ ] `409 map_version_conflict` → 최신 맵 재조회 → 구조 유지 시 **validate 부터**, 구조 변경 시 **structure 부터** 한 번 재실행
- [ ] **두 번째 충돌은 `commit_conflict`** 로 종료
- [ ] `422 unknown_slot_id` → 카탈로그 재조회 후 1회 재시도 (3.06 위임)
- [ ] 응답을 `ai_experience_request.result` 에 저장

## Definition of Done

- [ ] SSE 가 끊겨도 commit 이 중복 실행되지 않는다 (메인이 `request_id` 기준 멱등)
- [ ] 커밋 성공 후 응답이 유실돼도 `GET /commit/{request_id}` 로 복구된다
- [ ] `409` 1회 복구와 2회째 최종 실패가 구분된다
- [ ] 재실행 진입점이 구조 변경 여부에 따라 달라진다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- 커밋 구간에서 취소되면 메인에는 반영됐는데 AI 는 실패로 아는 상태가 된다. shield 가 이 태스크에서 가장 중요한 한 줄이다.
- 3.06 은 HTTP 계층, 이 태스크는 graph 재실행 판단. 책임을 섞지 않는다.
