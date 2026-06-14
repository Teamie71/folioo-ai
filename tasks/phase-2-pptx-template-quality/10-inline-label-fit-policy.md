---
id: "2.10"
phase: 2
title: "inline_label_group fit policy와 layout_actions 계산"
spec: "specs/phase-2/03-pptx-deterministic-text-fit-preflight.md"
depends_on: ["2.07", "2.08"]
blocks: ["2.11", "2.13", "2.15"]
estimate: "M"
status: "done"
completed_at: "2026-06-14"
owner: ""
sprint: ""
---

# Task 2.10 — inline_label_group fit policy와 layout_actions 계산

> Spec: [`specs/phase-2/03-pptx-deterministic-text-fit-preflight.md`](../../specs/phase-2/03-pptx-deterministic-text-fit-preflight.md)
> GitHub Issue: [#265](https://github.com/Teamie71/folioo-ai/issues/265)

## 의존성

- 2.07 (inline_label_group 추론) — group metadata 와 linked background 관계가 필요하다.
- 2.08 (LLM Slot prompt capacity hint 적용) — 생성 전 length hint 와 생성 후 fit policy 가 같은 slot capacity 를 사용한다.

## 사전 준비

- [x] `resize_shape`, `resize_linked_shape`, `relayout_row` action payload 초안 정리
- [x] row overflow 시 약칭 재요청과 실패 처리 경계 정리

## 구현 체크리스트

- [x] chip text required width 계산과 linked background width 계산 구현
- [x] 같은 row item 들의 x 좌표, gap 축소, overlap 여부를 사전 계산
- [x] 성공 시 `resize_shape`, `resize_linked_shape`, `relayout_row` layout action 목록 생성
- [x] row overflow 는 약칭 fallback 또는 실패로 분류
- [x] `OpenAI API` 공백 포함 label 이 nowrap 으로 유지되는 테스트 추가

## Definition of Done

- [x] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [x] chip text box 와 background width 를 함께 조정하는 layout action 이 계산된다
- [x] 같은 row 의 chip 들이 relayout 후 겹치지 않는다
- [x] row overflow 는 렌더 전에 실패 또는 약칭 fallback 으로 분류된다

## 리스크 / 메모

- font shrink 보다 resize/relayout 을 우선한다. wrap 은 1차에서 허용하지 않는다.
