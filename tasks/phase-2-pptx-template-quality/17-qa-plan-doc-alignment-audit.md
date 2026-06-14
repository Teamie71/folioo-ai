---
id: "2.17"
phase: 2
title: "QA/생성 계획 문서 정합성 감사"
spec: "specs/phase-2/06-pptx-template-quality-doc-alignment.md"
depends_on: ["2.13", "2.15", "2.16"]
blocks: ["2.18"]
estimate: "S"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.17 — QA/생성 계획 문서 정합성 감사

> Spec: [`specs/phase-2/06-pptx-template-quality-doc-alignment.md`](../../specs/phase-2/06-pptx-template-quality-doc-alignment.md)

## 의존성

- 2.13 (Pipeline layout_actions 통합과 currentFills 계약 유지) — `currentFills` 와 internal `layout_actions` 분리 계약이 구현되어야 한다.
- 2.15 (Validator 품질 warning과 신규 템플릿 acceptance fixture) — QA 와 validator 의 책임 경계가 확정되어야 한다.
- 2.16 (template-system과 ooxml-editing 문서 업데이트) — 문서 용어 정합성 감사의 기준 문서가 먼저 업데이트되어야 한다.

## 사전 준비

- [ ] `docs/architecture/qa-and-guardrails.md` 의 fix-and-verify 설명 확인
- [ ] `docs/architecture/pptx-gen-plan-v6.md` 의 callback/API 잔재 확인

## 구현 체크리스트

- [ ] `qa-and-guardrails.md` 에 1차 QA 유지 범위와 deterministic preflight 책임 분리 명시
- [ ] QA 2차 suggested remedy 방향을 후속 범위로 정리
- [ ] `pptx-gen-plan-v6.md` 의 오래된 `/api/internal`, width/height/byteSize callback 잔재 정리
- [ ] `currentFills` 와 internal `layout_actions` 분리 계약을 generation plan 에 반영
- [ ] `schema_version`, `placeholder_text`, `layout_groups`, `fit_policy`, `layout_actions` 용어 일관성 감사

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] QA 문서가 1차에서 geometry action 을 직접 생성하지 않는다고 명시한다
- [ ] 생성 계획 문서에 `layout_actions` 가 callback payload 에 들어가지 않는다고 명시한다
- [ ] 네 아키텍처 문서의 v2 용어가 상충 없이 사용된다

## 리스크 / 메모

- 이 task 는 문서 정합성 감사다. 구현 변경이 필요하면 별도 issue 로 분리한다.
