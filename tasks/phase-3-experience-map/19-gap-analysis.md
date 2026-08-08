---
id: "3.19"
phase: 3
title: "gap 분석 노드"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.17"]
blocks: ["3.20"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.19 — gap 분석 노드

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 2-1, 5-10
> PR: EM-19 · 브랜치 `feat/{issue}-experience-map-gap-analysis`
> GitHub Issue: [#309](https://github.com/Teamie71/folioo-ai/issues/309)

## 의존성

- 3.17 (validate·graph 배선) — **validate 통과 시점에 확정된 커밋 items** 를 입력으로 받는다.

## 사전 준비

- [ ] 5-10 우선순위 5단계와 동순위 tie-break 규칙 확인
- [ ] `active_gap` 저장 위치(`ai_experience_session.active_gap jsonb`) 확인

## 구현 체크리스트

- [ ] `nodes/gap_analysis.py` + `prompts/gap_analysis.py`
- [ ] `nodes/suggestion_response.py`
- [ ] 입력은 **이번 턴에 커밋될 items** (현재 맵 전체가 아님)
- [ ] 우선순위 5단계에서 **최대 1개** 선택, 동순위는 tie-break 3규칙
- [ ] gap 을 만들지 않는 4가지 경우 처리
- [ ] gap 유형 `extend_block`(→ 정제) / `new_child_block`(→ 구조화)
- [ ] 제안 문구 — 한 문장, 직접적 질문, 평가 표현 지양
- [ ] gap 이 없으면 고정 문구 "더 정리하고 싶으신 내용이 있나요?"
- [ ] `ai_experience_session.active_gap` 저장, gap 없으면 `null`

## Definition of Done

- [ ] **방금 커밋한 내용을 누락으로 지적하지 않는다**
- [ ] gap 없음과 gap 분석 실패가 구분된다 (실패일 때만 이벤트 생략)
- [ ] 한 응답에 gap 이 2개 이상 나오지 않는다
- [ ] 생성한 gap 이 다음 턴 `active_gap` 으로 이어진다
- [ ] gap 분석·제안 생성에 자동 재시도가 적용되지 않는다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- ℹ️ **통합 문서 9절 개발 순서에 이 노드의 독립 항목이 없다.** 17번 coordinator 에 묻혀 있는데, 프롬프트가 있는 LLM 노드라 별도 태스크로 뺐다. 문서 개정 시 9절에 추가할 것.
- 현재 맵만 보고 분석하면 방금 채운 블록을 비어 있다고 제안하게 된다. 입력 범위가 이 태스크의 핵심이다.
