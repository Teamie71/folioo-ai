---
id: "2.15"
phase: 2
title: "Validator 품질 warning과 신규 템플릿 acceptance fixture"
spec: "specs/phase-2/05-pptx-template-validator-v2-strict-mode.md"
depends_on: ["2.10", "2.12", "2.14"]
blocks: ["2.17"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.15 — Validator 품질 warning과 신규 템플릿 acceptance fixture

> Spec: [`specs/phase-2/05-pptx-template-validator-v2-strict-mode.md`](../../specs/phase-2/05-pptx-template-validator-v2-strict-mode.md)

## 의존성

- 2.10 (inline_label_group fit policy) — row overflow 와 linked background 품질 위험을 validator warning 으로 연결한다.
- 2.12 (relayout_row와 marker color 대체 action) — marker color fallback 과 relayout 가능성을 품질 기준에 반영한다.
- 2.14 (Validator v2 계약 검증과 strict CLI) — warning/fail 승격 체계가 있어야 신규 템플릿 acceptance 를 고정할 수 있다.

## 사전 준비

- [ ] `docs/ppt-v3.pptx` 기반 `templates/ppt-v3/` 신규 템플릿 fixture 의 추적 여부와 테스트 비용 확인
- [ ] warning 메시지에 slide, shape, group 식별자를 포함하는 출력 형식 정리

## 구현 체크리스트

- [ ] editable slot 이 너무 좁은 경우 warning 또는 strict fail 로 보고
- [ ] `inline_label_group` 후보의 linked background 신뢰도가 낮은 경우 warning 또는 strict fail 로 보고
- [ ] `output_text_color` fallback 사용과 placeholder 잔존 위험 warning 추가
- [ ] `docs/ppt-v3.pptx` 기반 `templates/ppt-v3/` 신규 템플릿의 첫 슬라이드 chip 과 정량 성과 수치 acceptance fixture 추가
- [ ] mixed-color run, `#FE0000`/theme red, 예시 pair 누락은 정상 acceptance deck 과 분리한 invalid fixture 로 추가
- [ ] validator 결과를 structured output 으로 테스트 가능하게 정리

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] 품질 위험은 기본 모드에서 warning 으로 보고된다
- [ ] strict mode 는 정해진 warning 을 fail 로 승격한다
- [ ] 신규 템플릿 acceptance fixture 가 marker, chip group, output color 기준을 검증한다

## 리스크 / 메모

- validator 가 geometry 를 수정하지 않는다. 발견과 리포팅만 책임진다.
