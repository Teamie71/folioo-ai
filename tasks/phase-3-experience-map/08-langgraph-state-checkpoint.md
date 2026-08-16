---
id: "3.08"
phase: 3
title: "LangGraph 상태 정의와 checkpoint 격리"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.02"]
blocks: ["3.10", "3.11"]
estimate: "S"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.08 — LangGraph 상태 정의와 checkpoint 격리

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 7-1, 9절 6번
> PR: EM-08 · 브랜치 `feat/{issue}-experience-map-state`
> GitHub Issue: [#301](https://github.com/Teamie71/folioo-ai/issues/301)

## 의존성

- 3.02 (스키마·오류 모델) — 중간 산출물 타입을 사용한다.

## 사전 준비

- [x] 기존 `features/interview` 의 TypedDict 상태 패턴 확인
- [x] `common/checkpointer/factory.py` 의 PostgreSQL checkpointer 사용법 확인

## 구현 체크리스트

- [x] `state.py` — 세션(user/session/request ID), 입력(user message·화면 context·view)
- [x] 파일(파일 reference, 추출 결과), 라우팅(intent, 현재 노드)
- [x] 맵(map version, outline, 대상 활동, alias map)
- [x] gap(활성 gap, 분류 결과), 중간 산출(filtered/structured/refined items)
- [x] 검증(validation errors, 보정 횟수), 실패(failed node, node retry count)
- [x] `thread_id = session_id`, `checkpoint_ns = experience_map`
- [x] 요청 시작 시 turn 전용 필드 초기화 헬퍼
- [x] 성공 뒤 파일 reference 와 큰 중간 산출물 정리

## Definition of Done

- [x] 요청 시작 시 turn 전용 필드가 초기화된다
- [x] 이전 대화는 유지되고 새 요청의 중간 필드는 섞이지 않는다
- [ ] 실패 superstep 을 `ainvoke(None, config)` 로 이어서 실행할 수 있다 — **3.17(graph 배선)에서 검증**. 이 태스크에서는 `build_thread_config()` 로 thread_id·checkpoint_ns 만 확정했다
- [x] checkpoint 에 직렬화 불가능한 값이 들어가지 않는다
- [x] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- checkpoint status 를 API 상태로 사용하지 않는다 (7-3). 상태의 기준은 `ai_experience_request` 다.
- 결과: `1075 passed` (신규 17). ruff check·format 통과.
- `cleanup_after_success()` 는 대상 필드가 원래 없었어도 기본값으로 채운다. 테스트가 부재 필드를 잡아내서 고쳤다 — 정리 뒤에는 항상 '비어 있음'이 명시돼야 읽는 쪽이 존재 여부를 확인하지 않는다.
