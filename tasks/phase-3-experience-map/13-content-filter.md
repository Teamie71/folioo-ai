---
id: "3.13"
phase: 3
title: "반영 내용 필터링 노드"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.11"]
blocks: ["3.14", "3.15"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.13 — 반영 내용 필터링 노드

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 5-3, 9절 11번
> PR: EM-13 · 브랜치 `feat/{issue}-experience-map-content-filter`
> GitHub Issue: [#307](https://github.com/Teamie71/folioo-ai/issues/307)

## 의존성

- 3.11 (Router·Fallback) — Router 의 `chat_input` 목적지이며, 전부 반영 제외면 fallback 으로 되돌아간다.

## 사전 준비

- [ ] 5-3 분류 3종 기준과 후속 노드 분기 5가지 확인
- [ ] 경험정리 내용 조회 tool 의 **조건부 호출** 조건 확인

## 구현 체크리스트

- [ ] `nodes/content_filter.py` + `prompts/content_filter.py`
- [ ] 분류 3종: 활성 gap 답변 / 새로 반영할 내용 / 반영 제외
- [ ] 경험정리 내용 조회 tool — 기존 내용과의 비교가 필요할 때만 호출
- [ ] 후속 노드 분기 5가지 구현 (gap 답변만 / 새 내용만 / 구조화 필요 gap+새 내용 / 정제 필요 gap+새 내용 / 반영 제외만)
- [ ] 반영 제외 내용 폐기

## Definition of Done

- [ ] structured output schema 검증이 동작한다
- [ ] 모든 출력 item 을 원문 source 로 역추적할 수 있다
- [ ] `active_gap` 이 없을 때 gap 답변으로 분류되지 않는다
- [ ] 후속 노드 분기 5가지가 명세 표대로 동작한다
- [ ] 입력에 없는 역할·성과·수치가 생성되지 않는다
- [ ] 요구사항 텍스트("이 문서 정리해줘")가 반영 제외로 분류된다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **사용자가 반영을 요청한 내용은 반드시 "새로 반영할 내용"** 이다. 요청 문장 자체와 혼동하지 않도록 프롬프트에서 구분한다.
- tool 을 무조건 호출하면 토큰과 지연이 늘어난다. 조건부 호출을 지킨다.
