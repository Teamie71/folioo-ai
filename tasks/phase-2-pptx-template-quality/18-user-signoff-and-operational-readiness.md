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

- [x] `docs/ppt-v3.pptx` 기반 `templates/ppt-v3/` 신규 등록 템플릿은 repo 에 포함하는 운영 자산으로 두고, synthetic acceptance fixture 는 CI 계약 테스트로 유지한다.
- [x] 정상 acceptance fixture 는 runtime/example pair, 정확한 `#FF0000` marker, non-red fixed text, chip group, metric slot 을 포함한다.
- [x] fixed label 과 editable marker 가 한 텍스트 shape 안에 섞인 mixed-color run 은 PPTX 를 수동 분리하지 않고 코드가 처리한다.
- [x] `docs/ppt-v3.pptx` 는 `templates/ppt-v3/` 운영 자산으로 승격하고, synthetic fixture 는 acceptance 계약 테스트로 유지한다.
- [x] `#FE0000`/theme red, 예시 pair 누락 같은 negative case 는 정상 deck 과 분리한 invalid fixture 로 둔다. mixed-color run 자체는 더 이상 blanket invalid case 가 아니다.
- [x] 현재 rollout 은 `compile_template.py --strict` 와 기본 `validate_template.py` 를 통과 기준으로 둔다. `validate_template.py --strict` 는 inline label group 후보 신뢰도 warning 정리 후 별도 승격한다.
- [x] Main backend 계약은 유지한다. `currentFills` 에 `layout_actions` 를 넣지 않고 DTO/SSE 변경과 `runtime_template.pptx` 물리 생성을 피한다.
- [ ] 업데이트된 `template-system.md`, `ooxml-editing.md`, `qa-and-guardrails.md`, `pptx-gen-plan-v6.md` 를 사용자와 함께 검토한다.

## Definition of Done

- [ ] 사용자 확인이 필요한 항목이 별도 follow-up 없이 모두 결정되었다
- [ ] 운영/CI 반영 방식과 fixture 보관 정책이 문서 또는 task 코멘트에 남았다
- [ ] Phase 2 완료 후 바로 이슈/PR 종료 판단을 할 수 있다

## 승인 반영 기록

### 템플릿 자산 / fixture 정책

- `docs/ppt-v3.pptx` 는 실제 운영 템플릿의 source PPTX 로 사용한다.
- `templates/ppt-v3/` 는 repo 에 포함하는 운영 자산으로 등록했다.
  - `template.pptx`: `docs/ppt-v3.pptx` 복사본
  - `meta.json`: v2 compiler 산출물
  - `reference.json`: v2 compiler 산출물
  - `thumbnail.jpg`: 실제 render 기반 thumbnail
- 자동 테스트의 정상 acceptance 기준은
  `tests/test_features/test_visualization/test_template_registration.py` 의 synthetic
  `ppt-v3-acceptance` fixture 로 유지한다. 이 fixture 는 runtime/example pair, 정확한
  `#FF0000` marker, non-red fixed text, inline chip group, metric slot, `output_text_color`
  계약을 strict validator 로 고정한다.
- PDF/preview 샘플은 repo 에 커밋하지 않는다. 샘플 산출물은 기존 워커 파이프라인처럼 job output 으로
  GCS 에 저장한다.

### mixed-color marker 결정

- 한 텍스트 shape 안에 fixed label/bullet 과 editable marker 가 섞여 있어도 처리한다.
- red marker segment 가 1개이고 non-red fixed text 가 함께 있으면
  `text_replacement_mode: "marker_runs"` 로 기록한다. 런타임은 non-red run 을 보존하고
  `#FF0000` marker run 만 교체한다.
- `경험명 - 본인 역할` 처럼 non-red 구분자 사이에 여러 red marker segment 가 있으면
  `text_replacement_mode: "shape"` 로 기록한다. 이때 `placeholder_text` 는 전체 shape 텍스트를
  사용하므로 LLM 이 구분자와 문맥을 함께 참고한다.
- `#FE0000`/theme red marker, runtime/example pair 누락, v2 schema 위반은 계속 invalid fixture 와
  negative test 로 유지한다.

### CI / 운영 게이트

- 현재 운영 반영 기준은 `compile_template.py --strict` 와 기본 `validate_template.py` 통과다.
- `validate_template.py --strict` 는 아직 CI/운영 필수 게이트로 승격하지 않는다.
  `templates/ppt-v3/` 에는 mixed-color 오류가 아니라 `inline_label_group` 후보 신뢰도 strict warning 이
  남아 있으므로, 이 항목은 후속 품질 정리 후 승격한다.
- `output_text_color` fallback 은 strict 에서도 warning 으로 유지한다. 좁은 editable slot,
  invalid geometry, placeholder 잔존 위험, 낮은 linked background 신뢰도는 strict failure 로 본다.

### Main backend / runtime 계약

- Main backend 계약은 변경하지 않는다. `currentFills` 는 text/remove/chart 상태만 담고,
  `layout_actions`, geometry 좌표, `layout_groups`/`fit_policy` metadata 는 callback payload 나
  DB 에 저장하지 않는다.
- `runtime_template.pptx` 물리 파일은 Phase 2 범위에서 만들지 않는다. 기존 파이프라인처럼
  원본 `template.pptx` 를 unpack 한 뒤 선택 slide 만 작업 파일에 남긴다.
- 향후 Main backend DTO/SSE 변경이 불가피하면 Phase 2 후속 issue 로 분리한다.

### 문서 반영 상태

- `docs/architecture/template-system.md`: mixed-color marker convention,
  `text_replacement_mode`, `marker_runs`/`shape` 교체 범위, 디자이너 가이드 반영.
- `docs/architecture/ooxml-editing.md`: `apply_fills()` 의 marker run 단위 교체와 non-red run 보존
  규칙 반영.
- `docs/architecture/qa-and-guardrails.md`: 1차 Visual QA 책임 경계가 현재 구현과 일치해 변경 없음.
- `docs/architecture/pptx-gen-plan-v6.md`: v2 slot metadata 요약과 `apply_fills()` 플로우에
  mixed-color 처리 반영.

## 검토 패킷

| 구분 | 위치 / 명령 | 결과 |
|---|---|---|
| source PPTX | `docs/ppt-v3.pptx` | `templates/ppt-v3/template.pptx` 의 source |
| 운영 템플릿 자산 | `templates/ppt-v3/template.pptx`, `meta.json`, `reference.json`, `thumbnail.jpg` | repo 포함 완료 |
| legacy/reference template | `templates/origin/template.pptx`, `templates/origin/meta.json`, `templates/origin/thumbnail.jpg` | 개선 전 origin 자산 |
| 정상 acceptance fixture | `tests/test_features/test_visualization/test_template_registration.py::test_validate_template_directory_accepts_ppt_v3_chip_acceptance_fixture` | synthetic `ppt-v3-acceptance` strict 통과 |
| invalid marker fixtures | non-exact red / theme red / missing pair tests | negative case 로 유지 |
| mixed-color compiler tests | `tests/test_features/test_visualization/test_template_v2_compiler.py` | `marker_runs`, reference fallback, `shape` mode 고정 |
| mixed-color runtime tests | `tests/test_features/test_visualization/test_slide_editor.py` | non-red run 보존, marker run 교체 고정 |

## 검증

- `uv run python scripts/templates/compile_template.py templates/ppt-v3 --strict` — passed
- `uv run python scripts/templates/compile_template.py templates/ppt-v3 --check --strict` — passed
- `uv run python scripts/templates/validate_template.py templates/ppt-v3` — passed
- `uv run python scripts/templates/validate_template.py templates/ppt-v3 --strict` — expected failure
  - runtime slide 2, 6, 22 의 `inline_label_group` 후보 신뢰도 strict error
  - mixed-color marker 오류는 아님
- `uv run pytest tests/test_features/test_visualization/test_template_v2_compiler.py tests/test_features/test_visualization/test_template_registration.py tests/test_features/test_visualization/test_slide_editor.py -q` — 100 passed
- `uv run ruff format --check .` — 200 files already formatted
- `uv run ruff check .` — All checks passed
- `uv run pytest -q` — 959 passed

## 커밋 기록

- `f7b42e3 feat: support mixed-color pptx markers`
- `0eac20b feat: add ppt-v3 template assets`

## post-merge 운영자 조치

PR merge 후 운영자는 gcloud CLI 로 운영 bucket 에 `templates/ppt-v3/` 를 업로드한다.

```bash
gcloud storage rsync ./templates/ppt-v3/ "gs://${GCS_BUCKET}/templates/ppt-v3/"
```

## 되돌림 메모

- PR #291 에서 이 문서를 완료 처리했지만, 2.18 은 사용자 승인 자체가 완료 조건인 HITL task 다.
- 따라서 #291 에서 수집한 검토 패킷은 최신 구현/결정 기준으로 갱신하고, task 상태와 DoD 는 아직
  완료 처리하지 않는다.
- 사용자가 업데이트된 문서와 PR 상태를 확인한 뒤, 같은 2.18 task 를 다시 완료 처리한다.

## 리스크 / 메모

- 이 task 는 구현 blocker 를 만들기 위한 선행 작업이 아니라 최종 승인/운영 반영 확인이다. 구현 중 새 사용자 결정이 필요해지면 이 task 체크리스트에 추가한다.
- 2.18 승인 전 남은 작업은 업데이트된 문서 검토, PR 판단, merge 후 GCS 업로드 실행 여부 확인이다.
- `validate_template.py --strict` 의 `inline_label_group` 후보 신뢰도 strict error 는 이번 mixed-color
  수정 범위가 아니며, CI/운영 필수 게이트 승격 전 별도 정리한다.
