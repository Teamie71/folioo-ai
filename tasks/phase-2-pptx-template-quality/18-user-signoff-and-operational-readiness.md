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

- [ ] Phase 2 구현 결과, validator 결과, 문서 변경 diff 를 사용자 검토용으로 정리
- [ ] 사용자가 확인할 수 있는 샘플 PPTX/PDF/preview 산출물을 준비
- [ ] Phase 2 착수 전 확정된 사용자 결정사항을 이 task 에 기록
- [ ] 각 Phase 2 task 완료 시 새 사용자 확인사항이 생기면 이 task 의 체크리스트에 추가

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

## 리스크 / 메모

- 이 task 는 구현 blocker 를 만들기 위한 선행 작업이 아니라 최종 승인/운영 반영 확인이다. 구현 중 새 사용자 결정이 필요해지면 이 task 체크리스트에 추가한다.
