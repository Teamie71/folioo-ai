---
id: "2.11"
phase: 2
title: "SlideEditor resize layout action 적용"
spec: "specs/phase-2/04-pptx-layout-action-application.md"
depends_on: ["1.03", "2.10"]
blocks: ["2.12"]
estimate: "M"
status: "done"
completed_at: "2026-06-14"
owner: ""
sprint: ""
---

# Task 2.11 — SlideEditor resize layout action 적용

> Spec: [`specs/phase-2/04-pptx-layout-action-application.md`](../../specs/phase-2/04-pptx-layout-action-application.md)
> GitHub Issue: [#266](https://github.com/Teamie71/folioo-ai/issues/266)

## 의존성

- 1.03 (SlideEditor) — 기존 OOXML 편집기 안에 geometry action 적용 메서드를 추가한다.
- 2.10 (inline_label_group fit policy) — 계산된 action payload 형식을 적용 대상으로 삼는다.

## 사전 준비

- [x] `<a:xfrm>`, `<a:off>`, `<a:ext>` 수정 방식과 기존 좌표 추출 유틸 확인
- [x] resize 대상 shape 와 linked background fixture 준비

## 구현 체크리스트

- [x] `SlideEditor.apply_layout_actions()` 메서드 골격 추가
- [x] `resize_shape` action 으로 shape width/height 를 OOXML 에 반영
- [x] `resize_linked_shape` action 으로 text shape 와 background shape 크기를 함께 수정
- [x] 잘못된 shape id/action payload 는 slide-level 오류로 반환 또는 raise
- [x] `tests/test_features/test_visualization/test_slide_editor.py` 에 resize action 테스트 추가

## Definition of Done

- [x] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [x] `resize_shape` 적용 후 OOXML ext 값이 변경된다
- [x] `resize_linked_shape` 적용 후 text box 와 background 폭이 모두 변경된다
- [x] 기존 `apply_fills()` 의 text/remove/chart 동작은 회귀하지 않는다

## 리스크 / 메모

- Geometry 변경은 `apply_fills()` 와 분리한다. 기존 fill API 에 action 을 섞지 않는다.
- 새 사용자 확인사항은 없었다.

## 검증

- `uv run pytest tests/test_features/test_visualization/test_slide_editor.py -q`
- `uv run pytest tests/test_features/test_visualization -q`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `git diff --check`
- `uv run pytest -q`
