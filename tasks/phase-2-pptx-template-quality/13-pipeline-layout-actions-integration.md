---
id: "2.13"
phase: 2
title: "Pipeline layout_actions 통합과 currentFills 계약 유지"
spec: "specs/phase-2/04-pptx-layout-action-application.md"
depends_on: ["1.05", "2.10", "2.12"]
blocks: ["2.17"]
estimate: "M"
status: "done"
completed_at: "2026-06-14"
owner: ""
sprint: ""
---

# Task 2.13 — Pipeline layout_actions 통합과 currentFills 계약 유지

> Spec: [`specs/phase-2/04-pptx-layout-action-application.md`](../../specs/phase-2/04-pptx-layout-action-application.md)
> GitHub Issue: [#268](https://github.com/Teamie71/folioo-ai/issues/268)

## 의존성

- 1.05 (초기 생성 파이프라인) — Step 3 의 slot extraction, LLM fill, OOXML 적용 흐름에 preflight 를 삽입한다.
- 2.10 (inline_label_group fit policy) — preflight 가 계산한 layout action 을 pipeline 에 전달한다.
- 2.12 (relayout_row와 marker color 대체 action) — SlideEditor 가 action 을 실제 OOXML 에 적용할 수 있어야 한다.

## 사전 준비

- [x] 생성과 재생성 pipeline 의 공통 편집 경로 확인
- [x] Main callback payload 테스트에서 `currentFills` 구조 검증 위치 확인

## 구현 체크리스트

- [x] LLM fill 결정 후 `layout_actions` 계산 단계를 추가
- [x] `apply_layout_actions()` 를 `apply_fills()` 전에 호출하도록 pipeline 순서 변경
- [x] `layout_actions` 는 워커 내부 객체로만 전달하고 callback payload 에 넣지 않도록 guard 추가
- [x] layout action 실패 시 해당 slide error 로 격리
- [x] pack/validate/render 통합 테스트 또는 pipeline fake 테스트 추가

## Definition of Done

- [x] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [x] `currentFills` callback payload 에 `layout_actions` 가 포함되지 않는다
- [x] layout action 결과가 최종 PPTX/PDF/preview 생성 경로에 반영된다
- [x] layout action 실패가 전체 job retry 로 번지지 않고 slide-level 오류로 수렴한다

## 검증 기록

- `uv run pytest tests/test_pptx_worker/test_visualization/test_generation_pipeline.py -q` — 27 passed
- `uv run pytest tests/test_pptx_worker/test_visualization tests/test_features/test_visualization -q` — 290 passed
- `uv run pytest -q` — 935 passed
- `uv run ruff check apps/pptx-worker/features/visualization/generation_pipeline.py tests/test_pptx_worker/test_visualization/test_generation_pipeline.py` — passed
- `uv run ruff format --check apps/pptx-worker/features/visualization/generation_pipeline.py tests/test_pptx_worker/test_visualization/test_generation_pipeline.py` — 2 files already formatted
- `uv run ruff check .` — passed
- `uv run ruff format --check .` — 200 files already formatted
- `git diff --check` — passed
- subagent review 1차 지적 반영 — QA outcome/current_fills sanitize, 재생성 layout action failure 격리, 저장 current_fills 오염 방지 테스트 추가
- subagent review 2차 — 추가 발견 없음, 1차 지적 3건 해소 확인

## 리스크 / 메모

- 메인 백엔드 계약은 바꾸지 않는다. Geometry state 는 최종 PPTX 에만 반영한다.
- 실제 렌더 픽셀 검증은 포함하지 않고, OOXML action 자체는 2.11/2.12 SlideEditor 테스트에 의존한다.
