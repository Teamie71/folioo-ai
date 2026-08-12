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

- [x] 5-3 분류 3종 기준과 후속 노드 분기 5가지 확인
- [x] 경험정리 내용 조회 tool 의 **조건부 호출** 조건 확인

## 구현 체크리스트

- [x] `nodes/content_filter.py` + `prompts/content_filter.py`
- [x] 분류 3종: 활성 gap 답변 / 새로 반영할 내용 / 반영 제외
- [ ] 경험정리 내용 조회 tool — 기존 내용과의 비교가 필요할 때만 호출 — **3.05 대기.** 맵을 읽으려면 `block`/`block_kind` 가 필요하다
- [x] 후속 노드 분기 5가지 구현 (gap 답변만 / 새 내용만 / 구조화 필요 gap+새 내용 / 정제 필요 gap+새 내용 / 반영 제외만)
- [x] 반영 제외 내용 폐기

## Definition of Done

- [x] structured output schema 검증이 동작한다
- [x] 모든 출력 item 을 원문 source 로 역추적할 수 있다
- [x] `active_gap` 이 없을 때 gap 답변으로 분류되지 않는다
- [x] 후속 노드 분기 5가지가 명세 표대로 동작한다
- [x] 입력에 없는 역할·성과·수치가 생성되지 않는다
- [x] 요구사항 텍스트("이 문서 정리해줘")가 반영 제외로 분류된다
- [x] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **사용자가 반영을 요청한 내용은 반드시 "새로 반영할 내용"** 이다. 요청 문장 자체와 혼동하지 않도록 프롬프트에서 구분한다.
- tool 을 무조건 호출하면 토큰과 지연이 늘어난다. 조건부 호출을 지킨다.
- 결과: `1252 passed` (신규 21). ruff check·format 통과.
- **LLM 출력을 그대로 믿지 않는다.** 두 가지를 코드로 강제한다.
  - **원문 역추적** — 모든 조각이 입력에 실제로 있는 문장이어야 한다. 없으면 그 조각만 버린다. 여기서 지어낸 수치가 통과하면 이후 노드는 그것을 사실로 다룬다. 공백 차이는 흡수한다.
  - **gap 이 없으면 gap 답변도 없다** — 활성 gap 이 없는데 gap 답변이 오면 새 내용으로 옮긴다. 사용자 입력인 것은 맞으므로 버리지 않는다.
- 같은 문장이 두 목록에 오면 먼저 온 쪽만 남긴다.
- 프롬프트는 gap 이 없을 때 **없다고 명시**한다. 없는데 있는 척하면 아무 문장이나 gap 답변이 된다.
- 경험정리 내용 조회 tool 은 3.05(맵 Repository) 대기다. `block`/`block_kind` DDL 이 없으면 맵을 읽을 수 없다. tool 없이도 나머지 분류는 동작한다.
