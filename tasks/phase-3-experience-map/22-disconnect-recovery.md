---
id: "3.22"
phase: 3
title: "연결 종료 처리와 lease 만료 복구"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.20"]
blocks: ["3.23"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.22 — 연결 종료 처리와 lease 만료 복구

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 9절 20번
> PR: EM-22 · 브랜치 `feat/{issue}-experience-map-disconnect-recovery`
> GitHub Issue: [#310](https://github.com/Teamie71/folioo-ai/issues/310)

## 의존성

- 3.20 (결과·coordinator) — 커밋 전/후 경계가 정의돼야 종료 처리가 갈린다.

## 사전 준비

- [ ] 커밋 전·후 경계와 shield 구간 확인 (3.18)
- [ ] 프론트 폴링 정책과 재시도 버튼 노출 조건 합의

## 구현 체크리스트

- [ ] 연결 종료가 **커밋 전**이면 실행 취소 후 failed 저장
- [ ] commit task 는 shield 처리 (취소되지 않음)
- [ ] 연결 종료가 **커밋 후**면 suggestion 을 생략하고 completed 저장
- [ ] request GET API 로 저장 결과 복구
- [ ] **만료 lease 정리 시 `GET /commit/{request_id}` 를 먼저 확인**
  - [ ] `committed: true` → 결과를 채우고 completed
  - [ ] `committed: false` → retryable failed
- [ ] lease 가 살아 있으면 `running` 유지

## Definition of Done

- [ ] 파일처리·LLM·커밋 각 시점에서 연결을 끊는 테스트가 통과한다
- [ ] 재접속 뒤 중복 block 없이 같은 결과를 조회할 수 있다
- [ ] 커밋 성공 + 응답 유실 상태가 lease 만료 후 completed 로 정리된다
- [ ] 재시도 버튼이 `failed` 에서만 노출된다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- 가장 위험한 상태는 "메인에는 커밋됐는데 AI 는 실패로 아는" 경우다. lease 정리에서 `GET /commit/{request_id}` 를 먼저 확인하는 순서가 이를 막는다.
