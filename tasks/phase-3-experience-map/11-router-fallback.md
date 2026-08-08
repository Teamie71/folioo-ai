---
id: "3.11"
phase: 3
title: "Router 와 Fallback"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.08", "3.10"]
blocks: ["3.12", "3.13"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.11 — Router 와 Fallback

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 5-1, 5-11, 9절 9·18번
> PR: EM-11 · 브랜치 `feat/{issue}-experience-map-router-fallback`

## 의존성

- 3.08 (LangGraph 상태), 3.10 (API·SSE 뼈대)

## 사전 준비

- [ ] 5-1 의 `out_of_scope` 명확한 대상 5가지 확인
- [ ] 5-11 의 진입 경로별 고정 문구 4종 확인

## 구현 체크리스트

- [ ] `nodes/router.py` + `prompts/router.py`
- [ ] `file_input` 은 **코드로 판정**, `chat_input`/`out_of_scope` 만 LLM 분류
- [ ] `out_of_scope` 판정을 **보수적으로** — 반영 여지가 있으면 `content_filter`
- [ ] gap 답변 여부는 Router 가 판정하지 않음 (활성 gap 이 있어도 `content_filter`)
- [ ] `nodes/fallback.py` — LLM 미사용, 진입 경로별 고정 문구
- [ ] `fallback_reason` 4종: `out_of_scope`·`file_unreadable`·`nothing_to_apply`·`ambiguous_target`
- [ ] DB 를 수정하지 않고 `message_complete(committed=false)` 후 completed 저장

## Definition of Done

- [ ] 진입 경로 4가지가 각각 자기 문구를 내보낸다 (문구 하나로 합쳐지지 않음)
- [ ] 어느 경로든 DB 변경 없이 completed 로 저장되고 재시도 버튼이 노출되지 않는다
- [ ] LLM 분류가 자동 재시도 후에도 실패하면 fallback 으로 간다
- [ ] `active_gap` 이 있어도 Router 가 gap 답변으로 분기하지 않는다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **Fallback 을 Router 와 같은 태스크로 묶었다.** Fallback 이 Router 의 목적지 중 하나라 따로 머지하면 어느 쪽도 end-to-end 로 돌지 않는다. 이 태스크가 mock 없이 도는 첫 경로다 — 채팅 입력 → `out_of_scope` → fallback → `message_complete`.
- Fallback 은 **실패가 아니다.** 재시도 버튼을 노출하면 사용자는 같은 결과를 반복해서 본다.
