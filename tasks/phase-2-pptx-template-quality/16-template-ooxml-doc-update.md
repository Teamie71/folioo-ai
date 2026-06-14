---
id: "2.16"
phase: 2
title: "template-system과 ooxml-editing 문서 업데이트"
spec: "specs/phase-2/06-pptx-template-quality-doc-alignment.md"
depends_on: ["2.04", "2.12"]
blocks: ["2.17"]
estimate: "S"
status: "done"
completed_at: "2026-06-14"
owner: ""
sprint: ""
---

# Task 2.16 — template-system과 ooxml-editing 문서 업데이트

> Spec: [`specs/phase-2/06-pptx-template-quality-doc-alignment.md`](../../specs/phase-2/06-pptx-template-quality-doc-alignment.md)
> GitHub Issue: [#271](https://github.com/Teamie71/folioo-ai/issues/271)

## 의존성

- 2.04 (compile_template CLI check 모드와 런타임 v2 fail-fast) — 문서에 기록할 v2 metadata 산출물과 CLI 정책이 확정되어야 한다.
- 2.12 (relayout_row와 marker color 대체 action) — OOXML action 과 marker color 대체 책임이 확정되어야 한다.

## 사전 준비

- [x] `docs/architecture/template-system.md` 의 기존 meta.json 설명 확인
- [x] `docs/architecture/ooxml-editing.md` 의 SlideEditor 책임 경계 확인

## 구현 체크리스트

- [x] `template-system.md` 에 짝수/홀수 slide pair convention 문서화
- [x] `template-system.md` 에 `#FF0000` marker 와 `meta.json`/`reference.json` v2 slot 계약 추가
- [x] `ooxml-editing.md` 에 `apply_layout_actions()` 책임과 action 종류 추가
- [x] `ooxml-editing.md` 에 marker color 대체, `item_background`, `container_shape` 처리 규칙 추가

## Definition of Done

- [x] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다. (신규 확인사항 없음)
- [x] 두 문서가 v2 metadata 와 marker convention 을 같은 의미로 설명한다
- [x] `layout_actions` 와 `currentFills` 의 책임 경계가 문서화된다
- [x] 문서 내용이 구현된 action 이름과 일치한다

## 검증 기록

- `rg -n 'schema_version|placeholder_text|layout_groups|fit_policy|layout_actions|currentFills|#FF0000|item_background|container_shape|apply_layout_actions|resize_shape|resize_linked_shape|relayout_row' docs/architecture/template-system.md docs/architecture/ooxml-editing.md` — 필수 용어 반영 확인
- `rg -n 'build_meta|런타임에 슬라이드 XML|Source Slide 별 메타데이터|set_text_color|compile_template\.py templates/.+--strict' docs/architecture/template-system.md docs/architecture/ooxml-editing.md` — 오래된 계약 잔재 없음
- 최신 `origin/dev` rebase 후 `scripts/templates/validate_template.py --strict`, `generation_pipeline.py` 의 `apply_layout_actions`/`slot_metadata`/`currentFills` sanitize 구현과 문서 용어 정합성 재확인
- `uv run ruff check .` — passed
- `uv run ruff format --check .` — 200 files already formatted
- `uv run pytest -q` — 945 passed
- `git diff --check` — 통과
- subagent review 1차 지적 반영 — 2.13/2.14 merge 후 구현 전제 재확인, 런타임 XML 추론 제거 표현 보정
- subagent review 2차 지적 반영 — v2 `runtime_slides[].category` 를 validator fail 계약이 아닌 선택 보강값으로 정정

## 리스크 / 메모

- 문서는 구현과 같은 용어를 사용한다. 긴 코드 예시는 추가하지 않는다.
