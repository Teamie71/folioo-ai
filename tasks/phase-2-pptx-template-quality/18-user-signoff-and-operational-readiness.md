---
id: "2.18"
phase: 2
title: "사용자 확인사항 취합과 운영 반영 승인"
spec: "specs/phase-2/06-pptx-template-quality-doc-alignment.md"
depends_on: ["2.17"]
blocks: []
estimate: "S"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.18 — 사용자 확인사항 취합과 운영 반영 승인

> Spec: [`specs/phase-2/06-pptx-template-quality-doc-alignment.md`](../../specs/phase-2/06-pptx-template-quality-doc-alignment.md)
> GitHub Issue: [#274](https://github.com/Teamie71/folioo-ai/issues/274)

## 의존성

- 2.17 (QA/생성 계획 문서 정합성 감사) — 구현과 문서 정합성 감사가 끝난 뒤, 사용자가 확인해야 하는 자산/정책/운영 반영 항목을 한 번에 승인한다.

## 사전 준비

- [x] Phase 2 구현 결과, validator 결과, 문서 변경 diff 를 사용자 검토용으로 정리
- [x] 사용자가 확인할 수 있는 PPTX 자산과 PDF/preview 산출물 준비 정책을 정리
- [x] Phase 2 착수 전 확정된 사용자 결정사항을 이 task 에 기록
- [x] 각 Phase 2 task 완료 시 새 사용자 확인사항이 생기면 이 task 의 체크리스트에 추가

## 착수 전 확정사항

- [x] `templates/origin/` 은 개선 전 버전으로 본다. 개선 후 `docs/ppt-v3.pptx` 를 사용해 템플릿을 새로 등록한다.
- [x] 신규 템플릿 디렉터리와 ID 는 `templates/ppt-v3/`, `template_id: "ppt-v3"` 로 진행한다.
- [x] invalid fixture 는 정상 acceptance deck 과 분리한다.
- [x] `compile_template.py` 는 기본 in-place 갱신, `--out` 검수용 별도 출력, `--check` CI 최신성 검사, `--strict` 수동/배포 전 게이트 방식으로 진행한다.
- [x] v2 런타임은 `schema_version != 2` 면 fail fast 한다. v1 backward compatibility 는 Phase 2 범위에 넣지 않는다.
- [x] row overflow 는 resize/relayout → gap 축소 → 약칭 1회 재요청 → 그래도 실패 시 slide-level error 순서로 처리한다.
- [x] `output_text_color` 를 예시에서 못 가져오면 `#000000` 으로 fallback 하고 warning 을 남긴다.
- [x] strict mode 는 editable `unknown` layout, 낮은 linked background 신뢰도, fallback 없는 좁은 editable slot, placeholder 잔존 위험을 fail 로 승격한다. 예시 색상 fallback 은 warning 으로 유지한다.
- [x] Main backend 계약은 최대한 변경하지 않는다. `currentFills` 에 `layout_actions` 를 넣지 않고, DTO/SSE 변경과 `runtime_template.pptx` 물리 생성을 피한다.

## 구현 체크리스트

- [ ] `docs/ppt-v3.pptx` 기반 `templates/ppt-v3/` 신규 등록 템플릿을 acceptance fixture 로 유지할지, 별도 운영 자산으로 둘지 사용자 확인을 받는다.
- [ ] 정상 acceptance deck 이 runtime/example pair, 정확한 `#FF0000` marker, non-red fixed text, chip group, metric slot 을 포함하는지 사용자 확인을 받는다.
- [ ] `docs/ppt-v3.pptx` 또는 운영 acceptance deck 에서 fixed label 과 editable marker 가 한 텍스트 shape 안에 섞인 mixed-color run 을 분리할지 사용자 확인을 받는다. (2.02 compiler 는 mixed-color run 을 계약 위반으로 실패 처리한다.)
- [ ] 2.15 기준 `docs/ppt-v3.pptx` 는 mixed-color marker 오류로 정상 acceptance deck 이 아니므로, synthetic fixture 정책을 유지할지 실제 PPTX 를 보정해 acceptance 자산으로 승격할지 사용자 확인을 받는다.
- [x] mixed-color run, `#FE0000`/theme red, 예시 pair 누락 같은 negative case 는 정상 deck 과 분리한 invalid fixture 로 둔다.
- [ ] `compile_template.py --strict` 를 CI 필수 게이트로 승격할 rollout 시점을 사용자 확인을 받는다.
- [ ] Main backend 계약 변경이 불가피한 상황이 발견되면 변경 사유와 대안을 사용자에게 최종 확인받는다.
- [ ] 업데이트된 `template-system.md`, `ooxml-editing.md`, `qa-and-guardrails.md`, `pptx-gen-plan-v6.md` 를 사용자와 함께 검토한다.

## Definition of Done

- [ ] 사용자 확인이 필요한 항목이 별도 follow-up 없이 모두 결정되었다
- [ ] 운영/CI 반영 방식과 fixture 보관 정책이 문서 또는 task 코멘트에 남았다
- [ ] Phase 2 완료 후 바로 이슈/PR 종료 판단을 할 수 있다

## 승인 대기 권장안

### 템플릿 자산 / fixture 정책

- 권장: `docs/ppt-v3.pptx` 는 Phase 2 기준 **디자인 source asset** 으로 유지한다. 현재 파일은
  mixed-color marker 오류가 남아 있어 정상 acceptance deck 으로 승격하지 않는다.
- 권장: 자동 테스트의 정상 acceptance 기준은
  `tests/test_features/test_visualization/test_template_registration.py` 의 synthetic
  `ppt-v3-acceptance` fixture 로 유지한다. 이 fixture 는 runtime/example pair, 정확한
  `#FF0000` marker, non-red fixed text, inline chip group, metric slot, `output_text_color`
  계약을 strict validator 로 고정한다.
- 권장: `docs/ppt-v3.pptx` 를 실제 운영 템플릿으로 승격하려면 fixed label 과 editable marker 가
  한 텍스트 shape 안에 섞인 mixed-color run 을 먼저 분리한 뒤 `templates/ppt-v3/` 로 등록한다.
- 권장: mixed-color run, `#FE0000`/theme red marker, 예시 pair 누락은 정상 deck 에 넣지 않고
  invalid fixture / negative test 로 유지한다.
- 권장: PDF/preview 샘플은 signoff 전 binary artifact 로 커밋하지 않는다. 현재 source PPTX 가
  strict compile 을 통과하지 않으므로, 운영 샘플 PDF/preview 는 mixed-color 보정 후
  `templates/ppt-v3/` 승격 PR 에서 생성한다.

### CI / 운영 게이트

- 권장: `compile_template.py --strict` 와 `validate_template.py --strict` 는 사용자 승인 전에는
  수동/배포 전 게이트로 유지한다.
- 권장: strict mode 를 CI 필수 게이트로 승격하는 시점은 실제 `templates/ppt-v3/` 운영 자산이
  mixed-color 오류 없이 등록되고, synthetic fixture 외 실제 deck 기준 warning 이 정리된 뒤다.
- 권장: `output_text_color` fallback 은 strict 에서도 warning 으로 유지한다. 좁은 editable slot,
  invalid geometry, placeholder 잔존 위험, 낮은 linked background 신뢰도는 strict failure 로 본다.

### Main backend / runtime 계약

- 권장: Main backend 계약은 변경하지 않는다. `currentFills` 는 text/remove/chart 상태만 담고,
  `layout_actions`, geometry 좌표, `layout_groups`/`fit_policy` metadata 는 callback payload 나
  DB 에 저장하지 않는다.
- 권장: `runtime_template.pptx` 물리 파일은 Phase 2 범위에서 만들지 않는다. 기존 파이프라인처럼
  원본 `template.pptx` 를 unpack 한 뒤 선택 slide 만 작업 파일에 남긴다.
- 권장: 향후 Main backend DTO/SSE 변경이 불가피하면 Phase 2 후속 issue 로 분리한다.

### 문서 검토 결과

- `docs/architecture/template-system.md`: v2 pair convention, exact `#FF0000` marker,
  `meta.json`/`reference.json`, `placeholder_text`, `layout_groups`, `fit_policy` 계약 반영 완료.
- `docs/architecture/ooxml-editing.md`: `apply_layout_actions()`, marker color replacement,
  `item_background`/`container_shape`, `currentFills` 와 `layout_actions` 경계 반영 완료.
- `docs/architecture/qa-and-guardrails.md`: 1차 Visual QA 는 geometry action 을 직접 만들지
  않는 검증자이고, 2차 `suggested_remedy` 는 후속 범위라고 명시 완료.
- `docs/architecture/pptx-gen-plan-v6.md`: v2 metadata, Step 3 deterministic preflight,
  callback payload 에 `layout_actions` 를 넣지 않는 계약, preview size field 제외 반영 완료.

## 검토 패킷

| 구분 | 위치 / 명령 | 결과 |
|---|---|---|
| source PPTX | `docs/ppt-v3.pptx` | 운영 승격 전 보정 대상 |
| legacy/reference template | `templates/origin/template.pptx`, `templates/origin/meta.json`, `templates/origin/thumbnail.jpg` | 개선 전 origin 자산 |
| 정상 acceptance fixture | `tests/test_features/test_visualization/test_template_registration.py::test_validate_template_directory_accepts_ppt_v3_chip_acceptance_fixture` | synthetic `ppt-v3-acceptance` strict 통과 |
| invalid marker fixtures | 같은 테스트 파일의 mixed-color / non-exact red / missing pair tests | negative case 로 유지 |
| 실제 `docs/ppt-v3.pptx` strict compile | `uv run python scripts/templates/compile_template.py <tmp-ppt-v3-dir> --strict` | mixed-color marker 오류로 실패, 운영 승격 보류 |
| 문서 정합성 | `template-system.md`, `ooxml-editing.md`, `qa-and-guardrails.md`, `pptx-gen-plan-v6.md` | Phase 2 v2 계약 반영 완료 |

`docs/ppt-v3.pptx` strict compile 에서 확인한 대표 오류:

```text
ERROR: runtime slide 2 shape 3에 #FF0000 marker와 non-red run이 섞여 있습니다.
ERROR: runtime slide 20 shape 15에 #FF0000 marker와 non-red run이 섞여 있습니다.
ERROR: runtime slide 30 shape 19에 #FF0000 marker와 non-red run이 섞여 있습니다.
```

## 검증

- `uv run python scripts/templates/compile_template.py <tmp-ppt-v3-dir> --strict` — expected failure
  - `docs/ppt-v3.pptx` 는 mixed-color marker 오류가 있어 운영 acceptance deck 으로 승격하지 않음
- `uv run pytest tests/test_features/test_visualization/test_template_registration.py -q` — 47 passed
- `git diff --check`
- `uv run ruff format --check .` — 200 files already formatted
- `uv run ruff check .` — All checks passed
- `uv run pytest -q` — 954 passed

## 되돌림 메모

- PR #291 에서 이 문서를 완료 처리했지만, 2.18 은 사용자 승인 자체가 완료 조건인 HITL task 다.
- 따라서 #291 에서 수집한 검토 패킷과 권장안은 유지하되, task 상태와 DoD 는 다시 승인 대기로 되돌린다.
- 사용자가 권장안을 승인하거나 수정 결정을 내린 뒤, 같은 2.18 task 를 다시 완료 처리한다.

## 리스크 / 메모

- 이 task 는 구현 blocker 를 만들기 위한 선행 작업이 아니라 최종 승인/운영 반영 확인이다. 구현 중 새 사용자 결정이 필요해지면 이 task 체크리스트에 추가한다.
- 2.18 승인 후 남는 후속 작업은 새 기능 구현이 아니라 운영 자산 보정이다. `docs/ppt-v3.pptx` 의
  mixed-color run 을 분리하고 실제 `templates/ppt-v3/` 승격 PR 에서 PDF/preview 샘플을 생성한다.
