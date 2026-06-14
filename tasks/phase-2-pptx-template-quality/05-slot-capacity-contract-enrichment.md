---
id: "2.05"
phase: 2
title: "Slot capacity 계약 확장"
spec: "specs/phase-2/02-pptx-slot-layout-group-inference.md"
depends_on: ["2.03"]
blocks: ["2.06", "2.08"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.05 — Slot capacity 계약 확장

> Spec: [`specs/phase-2/02-pptx-slot-layout-group-inference.md`](../../specs/phase-2/02-pptx-slot-layout-group-inference.md)

## 의존성

- 2.03 (예시 슬라이드 reference 매칭) — example text, line count, output color 를 slot capacity 필드에 반영한다.

## 사전 준비

- [ ] v2 slot descriptor 필드 목록과 기본값 정책 확인
- [ ] 기존 LLM Slot descriptor 소비 코드가 unknown field 를 처리하는 방식 확인

## 구현 체크리스트

- [ ] slot 에 `example_text`, `example_char_count`, `example_line_count`, `output_text_color` 병합
- [ ] `min_font_pt`, `max_font_pt`, `max_lines`, `nowrap`, `fit_policy`, `allowed_actions` 기본 추론 추가
- [ ] `role_hint` 는 optional 로만 기록하고 누락 가능성을 테스트
- [ ] v2 slot descriptor serialization 테스트 추가
- [ ] 기존 `text`/`remove`/`chart` fill action 계약과 충돌하지 않는지 확인

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] v2 `meta.json` 의 editable slot 이 capacity 필드를 포함한다
- [ ] `role_hint` 가 없어도 slot payload 와 prompt 생성이 실패하지 않는다
- [ ] 기존 `currentFills` 구조가 slot capacity 확장으로 변경되지 않는다

## 리스크 / 메모

- `placeholder_text` 가 1차 content hint 이며, `role_hint` 에 의존하는 구현을 만들지 않는다.
