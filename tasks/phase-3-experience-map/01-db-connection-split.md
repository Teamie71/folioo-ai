---
id: "3.01"
phase: 3
title: "경험 맵 DB 연결 분리와 checkpointer 정리"
spec: "docs/architecture/experience-map-agent.md"
depends_on: []
blocks: ["3.04", "3.05"]
estimate: "S"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.01 — 경험 맵 DB 연결 분리와 checkpointer 정리

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 9절 2번
> PR: EM-01 · 브랜치 `chore/{issue}-experience-map-db-split`
> 분할 계획: [`docs/architecture/experience-map-pr-plan.md`](../../docs/architecture/experience-map-pr-plan.md)
> GitHub Issue: [#301](https://github.com/Teamie71/folioo-ai/issues/301)

## 의존성

- 없음. **기존 공용 코드를 건드리는 유일한 선행 작업**이므로 Phase 3에서 가장 먼저 머지한다.

## 사전 준비

- [x] 최근 커밋 `ac2e53b`(LangGraph 체크포인터 AsyncConnectionPool 교체)가 건드린 `common/checkpointer/factory.py` 현재 상태 확인
- [ ] local·dev·prod 각 환경에 `CHECKPOINT_DATABASE_URL` 이 설정돼 있는지 확인 — **배포 담당 확인 필요**
- [x] 기존 `common/db/connection.py` 사용처 전수 조사

## 구현 체크리스트

- [x] `common/db/connection.py` 의 asyncpg pool 을 앱 lifespan 에서 생성·종료
- [x] `common/checkpointer/factory.py` 에서 `DATABASE_URL` fallback 제거
- [x] `CHECKPOINT_DATABASE_URL` 미설정 시 명시적 오류로 기동 실패
- [x] pool 크기와 statement timeout 설정값 노출 (`DB_POOL_MIN_SIZE`·`DB_POOL_MAX_SIZE`·`DB_STATEMENT_TIMEOUT_MS`)
- [x] `/health` 에 경험 맵 DB 연결 상태 추가 (`experience_map_db`)
- [x] `.env.example` 갱신

## Definition of Done

- [x] 경험 맵 DB 에 LangGraph checkpoint 테이블이 생성되지 않는다
- [x] 앱 종료 시 두 DB pool 이 모두 닫힌다
- [x] `CHECKPOINT_DATABASE_URL` 없이 기동하면 fallback 없이 실패한다
- [x] `ruff check .` · `ruff format --check .` · `pytest` 통과 (982 passed)

## 리스크 / 메모

- ⚠️ **fallback 제거는 배포 환경변수를 깨뜨린다.** 환경변수 선반영 → 코드 배포 순서를 PR 설명에 명시할 것.
- 같은 파일을 최근에 고친 커밋이 있어 rebase 충돌 가능. 브랜치를 dev 최신으로 맞춘 뒤 시작한다.
- 조사 결과 `common/db/connection.py` 는 **어디서도 호출되지 않는 사장 코드**였다. 이 태스크가 최초 배선이다.
- **`DATABASE_URL` 누락은 기동을 막지 않는다** (경고 후 계속, `/health` 에 `disconnected`). 통합 문서 9절 2번이 fatal 로 규정한 것은 `CHECKPOINT_DATABASE_URL` 뿐이고, 경험 맵 기능은 3.23 까지 flag 뒤에 있으므로 기존 인터뷰 챗 배포를 깨뜨리지 않기 위함이다.
