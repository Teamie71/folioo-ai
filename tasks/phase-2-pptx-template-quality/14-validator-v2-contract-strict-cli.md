---
id: "2.14"
phase: 2
title: "Validator v2 계약 검증과 strict CLI"
spec: "specs/phase-2/05-pptx-template-validator-v2-strict-mode.md"
depends_on: ["2.04", "2.07"]
blocks: ["2.15"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.14 — Validator v2 계약 검증과 strict CLI

> Spec: [`specs/phase-2/05-pptx-template-validator-v2-strict-mode.md`](../../specs/phase-2/05-pptx-template-validator-v2-strict-mode.md)

## 의존성

- 2.04 (compile_template CLI check 모드와 런타임 v2 fail-fast) — validator 가 v2 산출물 구조와 check 결과를 기준으로 동작한다.
- 2.07 (inline_label_group 추론) — strict mode 에서 `unknown` layout 과 group 신뢰도를 판단한다.

## 사전 준비

- [ ] 기존 `validate_template.py` 와 `features.visualization.templates.validation` 책임 확인
- [ ] 기본 모드 fail 항목과 strict 승격 항목 목록 확정

## 구현 체크리스트

- [ ] `validate_template.py --strict` 인자 추가
- [ ] `template.pptx`, `meta.json`, 선택적 `reference.json` 의 `schema_version: 2` 구조 검증
- [ ] runtime/example slide 분리, runtime 대상 존재, example runtime 포함 금지 검증
- [ ] `#FF0000` marker 없음, mixed-color run, 예시 매칭 실패를 기본 fail 로 처리
- [ ] editable `unknown` layout 은 strict mode 에서 fail 로 승격

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] 기본 모드와 strict mode 의 exit code 차이가 테스트로 고정된다
- [ ] 예시 슬라이드가 runtime 후보에 포함되면 실패한다
- [ ] mixed-color run fixture 가 validator 실패로 보고된다

## 리스크 / 메모

- 기존 category/thumbnail 검증은 유지한다. v2 slot/layout 검증을 추가하는 방향이다.
