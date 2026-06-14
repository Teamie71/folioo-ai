---
id: "2.17"
phase: 2
title: "QA/생성 계획 문서 정합성 감사"
spec: "specs/phase-2/06-pptx-template-quality-doc-alignment.md"
depends_on: ["2.13", "2.15", "2.16"]
blocks: ["2.18"]
estimate: "S"
status: "done"
owner: ""
sprint: ""
---

# Task 2.17 — QA/생성 계획 문서 정합성 감사

> Spec: [`specs/phase-2/06-pptx-template-quality-doc-alignment.md`](../../specs/phase-2/06-pptx-template-quality-doc-alignment.md)
> GitHub Issue: [#273](https://github.com/Teamie71/folioo-ai/issues/273)

## 의존성

- 2.13 (Pipeline layout_actions 통합과 currentFills 계약 유지) — `currentFills` 와 internal `layout_actions` 분리 계약이 구현되어야 한다.
- 2.15 (Validator 품질 warning과 신규 템플릿 acceptance fixture) — QA 와 validator 의 책임 경계가 확정되어야 한다.
- 2.16 (template-system과 ooxml-editing 문서 업데이트) — 문서 용어 정합성 감사의 기준 문서가 먼저 업데이트되어야 한다.

## 사전 준비

- [x] `docs/architecture/qa-and-guardrails.md` 의 fix-and-verify 설명 확인
- [x] `docs/architecture/pptx-gen-plan-v6.md` 의 callback/API 잔재 확인

## 구현 체크리스트

- [x] `qa-and-guardrails.md` 에 1차 QA 유지 범위와 deterministic preflight 책임 분리 명시
- [x] QA 2차 suggested remedy 방향을 후속 범위로 정리
- [x] `pptx-gen-plan-v6.md` 의 오래된 `/api/internal`, width/height/byteSize callback 잔재 정리
- [x] `currentFills` 와 internal `layout_actions` 분리 계약을 generation plan 에 반영
- [x] `schema_version`, `placeholder_text`, `layout_groups`, `fit_policy`, `layout_actions` 용어 일관성 감사

## Definition of Done

- [x] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [x] QA 문서가 1차에서 geometry action 을 직접 생성하지 않는다고 명시한다
- [x] 생성 계획 문서에 `layout_actions` 가 callback payload 에 들어가지 않는다고 명시한다
- [x] 네 아키텍처 문서의 v2 용어가 상충 없이 사용된다

## 변경 요약

- `qa-and-guardrails.md` 에 1차 Visual QA 책임 경계를 추가했다. Visual QA 는 렌더 이미지 검증자이고, geometry action 은 만들지 않으며 deterministic preflight 와 `apply_layout_actions()` 가 실제 geometry 변경을 담당한다.
- `pptx-gen-plan-v6.md` 의 템플릿/OOXML 요약과 Step 3 흐름을 v2 metadata 기준으로 정리했다. `meta.json` 은 `schema_version: 2` runtime 계약, `reference.json` 은 example slide 기반 audit/reference 산출물로 설명한다.
- Step 3 에서 LLM 은 text/remove/chart fill 만 만들고, `layout_actions` 는 내부 deterministic preflight 산출물로 계산·적용한다고 명시했다.
- worker callback 과 DB `currentFills` 에 `layout_actions`, geometry 좌표, `layout_groups`/`fit_policy` metadata 를 넣지 않는다고 명시했다.
- `slide_preview_ready` 예시에서 `width`/`height`/`byteSize` payload 를 제거하고, 남은 언급은 현재 callback/API 계약에 포함하지 않는다는 명시적 제외 문구로만 유지했다.
- `/api/internal/visualizations/...` 는 obsolete 공개 API 가 아니라 현재 worker→main callback/context API 로 유지한다고 정리했다.

## 검증

- `rg -n 'width, height, byteSize|width/height/byteSize|byteSize|runtime_template|build_meta|set_text_color' docs/architecture/pptx-gen-plan-v6.md docs/architecture/qa-and-guardrails.md docs/architecture/template-system.md docs/architecture/ooxml-editing.md`
  - `width`/`height`/`byteSize` 는 명시적 제외 문구로만 남음
  - `runtime_template`, `build_meta`, `set_text_color` 잔재 없음
- `rg -n 'meta\.json 은 Source Slide|사전 Slot|런타임에 시각화 워커가 슬라이드 XML|SlideEditor\.extract_slots\(\).*apply_fills|geometry action 을 직접 생성하지 않는다|layout_actions.*callback|preview callback' docs/architecture/pptx-gen-plan-v6.md docs/architecture/qa-and-guardrails.md`
  - stale v1/동적 slot 설명 없음
  - QA geometry action 금지와 callback 제외 계약 확인
- `git diff --check`
- `uv run ruff format --check .` — 200 files already formatted
- `uv run ruff check .` — All checks passed
- `uv run pytest -q` — 954 passed

## 리뷰

- 1차 subagent review: 발견 사항 없음. 문서 변경만 수동 대조했고 실행 테스트 갭은 위 검증으로 보완.
- 2차 review: 1차에서 발견 사항이 없어 생략.

## 리스크 / 메모

- 이 task 는 문서 정합성 감사다. 구현 변경이 필요하면 별도 issue 로 분리한다.
- 이번 감사에서 새 사용자 확인사항은 발생하지 않았다. 2.18 의 기존 승인 항목으로 충분하다.
