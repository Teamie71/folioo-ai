---
id: "3.21"
phase: 3
title: "사용자 재시도와 checkpoint resume"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.20"]
blocks: ["3.23"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.21 — 사용자 재시도와 checkpoint resume

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 7-2, 9절 19번
> PR: EM-21 · 브랜치 `feat/{issue}-experience-map-user-retry`
> GitHub Issue: [#310](https://github.com/Teamie71/folioo-ai/issues/310)

## 의존성

- 3.20 (결과·coordinator) — 전체 흐름이 완성된 뒤에야 "실패 시점부터 재개"가 의미를 갖는다.

## 사전 준비

- [ ] 재시도 가능 조건(마지막 요청·failed·retryable·30분 TTL) 확인
- [ ] GCS object 1시간 TTL 과 재시도 30분 TTL 의 관계 정리

## 구현 체크리스트

- [ ] 마지막 요청·failed 상태·retryable·30분 TTL 확인
- [ ] 텍스트 추출 미완료면 GCS object TTL 추가 확인
- [ ] request lease 재획득
- [ ] 최신 경험 맵과 map version 재조회
- [ ] checkpoint 의 실패 superstep 부터 `ainvoke(None, config)` 로 resume
- [ ] commit 결과가 이미 있으면 commit 을 건너뛰고 completed 로 복구
- [ ] `POST .../retry/stream` 엔드포인트 연결

## Definition of Done

- [ ] 성공한 이전 노드는 다시 실행하지 않는다
- [ ] 새 요청 시작 뒤 이전 실패 요청의 재시도가 거부된다
- [ ] 만료 상태는 `410 retry_expired` 를 반환한다
- [ ] 추출 완료 뒤에는 GCS 원본 없이 재시도된다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- 유저 재시도는 처음부터 돌리지 않는다. 실패 시점 checkpoint 를 불러와 이어서 실행한다 — 비용과 지연 모두에 영향이 크다.
- Fallback 은 실패가 아니므로 재시도 대상이 아니다 (3.11).
