---
id: "3.20"
phase: 3
title: "결과 응답 생성과 커밋·gap 병렬 coordinator"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.18", "3.19"]
blocks: ["3.21", "3.22", "3.23"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.20 — 결과 응답 생성과 커밋·gap 병렬 coordinator

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 5-9, 9절 17번
> PR: EM-20 · 브랜치 `feat/{issue}-experience-map-coordinator`

## 의존성

- 3.18 (커밋 위임), 3.19 (gap 분석) — 두 task 를 병렬로 돌린다.

## 사전 준비

- [ ] 5-9 결과 응답 변수 5종과 템플릿 3종 확인
- [ ] LangGraph fan-out 이 superstep 경계에서 합류한다는 제약 재확인

## 구현 체크리스트

- [ ] `nodes/result_response.py` — **LLM 미사용 결정적 템플릿**
- [ ] 변수 `{experience_name}`·`{category_label}`·`{added_count}`·`{updated_count}`·`{dropped_count}` 분리
- [ ] 템플릿 3종 (단일 카테고리 / 여러 카테고리 / 기존 블록만 수정)
- [ ] 탈락 항목이 있으면 안내 문구 덧붙임
- [ ] 경로(`{experience_name} > {category_label}`) 반드시 포함
- [ ] `coordinator.py` — commit task 와 gap task 동시 시작
- [ ] commit await → 실패 시 gap task 취소 후 failed/error, 성공 시 `commit_result` → result message
- [ ] gap await → 실패 시 이벤트 생략, 성공 시 `suggestion_ready` → suggestion message
- [ ] request completed → `processing_complete`
- [ ] 두 task 가 서로 다른 state 필드에만 쓰도록 분리

## Definition of Done

- [ ] 느린 gap 분석이 결과 응답 전송을 지연하지 않는다
- [ ] gap 실패가 완료 요청을 failed 로 바꾸지 않는다
- [ ] 커밋 실패 뒤 suggestion 이벤트가 전송되지 않는다
- [ ] `dropped_count` 가 있으면 안내 문구가 덧붙는다
- [ ] 결과 응답에 LLM 호출이 없다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **graph fan-out 으로 연결하지 않는다.** LangGraph 는 superstep 이 모두 끝나야 다음으로 넘어가므로 느린 gap 분석이 결과 응답을 붙잡는다. service coordinator 가 처리한다.
- 결과 문구는 **초안**이다 (10절 2번). 변수를 분리해 두면 기획 확정 시 문구만 교체된다 — 이 태스크에서 그 분리를 지킨다.
- 사전 승인이 없는 구조라 이 문구가 **사용자가 오배정을 발견하는 주 경로**다. 경로 표기를 생략하지 않는다.
