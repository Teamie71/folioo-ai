---
id: "2.07"
phase: 2
title: "inline_label_group 추론"
spec: "specs/phase-2/02-pptx-slot-layout-group-inference.md"
depends_on: ["2.06"]
blocks: ["2.10", "2.14"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.07 — inline_label_group 추론

> Spec: [`specs/phase-2/02-pptx-slot-layout-group-inference.md`](../../specs/phase-2/02-pptx-slot-layout-group-inference.md)

## 의존성

- 2.06 (item_background와 container_shape 추론) — inline label group 은 text slot 과 linked background 관계를 함께 사용한다.

## 사전 준비

- [ ] 첫 슬라이드 하단 기술 스택 chip acceptance fixture 확인
- [ ] flow, gap, row alignment, wrap_allowed 기본 정책 정리

## 구현 체크리스트

- [ ] 반복되는 짧은 editable slot 의 bbox alignment 와 gap 을 기준으로 group 후보 탐지
- [ ] group 에 `group_id`, `layout_type`, `flow`, `item_shape_ids`, `gap_emu`, `min_gap_emu`, `wrap_allowed` 기록
- [ ] `linked_background_by_item` 을 group metadata 에 연결
- [ ] 신뢰도 낮은 후보는 `basic_text_area` 또는 `unknown` fallback 으로 둠
- [ ] `origin` 특정 shape id/slide index 없이 fixture 를 통과하는 테스트 추가

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] 첫 슬라이드 하단 chip 이 하나의 `inline_label_group` 으로 묶인다
- [ ] group item 과 linked background 관계가 metadata 에 함께 기록된다
- [ ] 애매한 후보는 group 으로 강제 분류되지 않는다

## 리스크 / 메모

- `layout_type` 은 slot 단위가 아니라 group 단위 개념이다.
