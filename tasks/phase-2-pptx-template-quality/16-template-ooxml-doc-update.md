---
id: "2.16"
phase: 2
title: "template-system과 ooxml-editing 문서 업데이트"
spec: "specs/phase-2/06-pptx-template-quality-doc-alignment.md"
depends_on: ["2.04", "2.12"]
blocks: ["2.17"]
estimate: "S"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.16 — template-system과 ooxml-editing 문서 업데이트

> Spec: [`specs/phase-2/06-pptx-template-quality-doc-alignment.md`](../../specs/phase-2/06-pptx-template-quality-doc-alignment.md)

## 의존성

- 2.04 (compile_template CLI check 모드와 런타임 v2 fail-fast) — 문서에 기록할 v2 metadata 산출물과 CLI 정책이 확정되어야 한다.
- 2.12 (relayout_row와 marker color 대체 action) — OOXML action 과 marker color 대체 책임이 확정되어야 한다.

## 사전 준비

- [ ] `docs/architecture/template-system.md` 의 기존 meta.json 설명 확인
- [ ] `docs/architecture/ooxml-editing.md` 의 SlideEditor 책임 경계 확인

## 구현 체크리스트

- [ ] `template-system.md` 에 짝수/홀수 slide pair convention 문서화
- [ ] `template-system.md` 에 `#FF0000` marker 와 `meta.json`/`reference.json` v2 slot 계약 추가
- [ ] `ooxml-editing.md` 에 `apply_layout_actions()` 책임과 action 종류 추가
- [ ] `ooxml-editing.md` 에 marker color 대체, `item_background`, `container_shape` 처리 규칙 추가

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] 두 문서가 v2 metadata 와 marker convention 을 같은 의미로 설명한다
- [ ] `layout_actions` 와 `currentFills` 의 책임 경계가 문서화된다
- [ ] 문서 내용이 구현된 action 이름과 일치한다

## 리스크 / 메모

- 문서는 구현과 같은 용어를 사용한다. 긴 코드 예시는 추가하지 않는다.
