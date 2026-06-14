---
id: "2.13"
phase: 2
title: "Pipeline layout_actions 통합과 currentFills 계약 유지"
spec: "specs/phase-2/04-pptx-layout-action-application.md"
depends_on: ["1.05", "2.10", "2.12"]
blocks: ["2.17"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.13 — Pipeline layout_actions 통합과 currentFills 계약 유지

> Spec: [`specs/phase-2/04-pptx-layout-action-application.md`](../../specs/phase-2/04-pptx-layout-action-application.md)

## 의존성

- 1.05 (초기 생성 파이프라인) — Step 3 의 slot extraction, LLM fill, OOXML 적용 흐름에 preflight 를 삽입한다.
- 2.10 (inline_label_group fit policy) — preflight 가 계산한 layout action 을 pipeline 에 전달한다.
- 2.12 (relayout_row와 marker color 대체 action) — SlideEditor 가 action 을 실제 OOXML 에 적용할 수 있어야 한다.

## 사전 준비

- [ ] 생성과 재생성 pipeline 의 공통 편집 경로 확인
- [ ] Main callback payload 테스트에서 `currentFills` 구조 검증 위치 확인

## 구현 체크리스트

- [ ] LLM fill 결정 후 `layout_actions` 계산 단계를 추가
- [ ] `apply_layout_actions()` 를 `apply_fills()` 전에 호출하도록 pipeline 순서 변경
- [ ] `layout_actions` 는 워커 내부 객체로만 전달하고 callback payload 에 넣지 않도록 guard 추가
- [ ] layout action 실패 시 해당 slide error 로 격리
- [ ] pack/validate/render 통합 테스트 또는 pipeline fake 테스트 추가

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] `currentFills` callback payload 에 `layout_actions` 가 포함되지 않는다
- [ ] layout action 결과가 최종 PPTX/PDF/preview 생성 경로에 반영된다
- [ ] layout action 실패가 전체 job retry 로 번지지 않고 slide-level 오류로 수렴한다

## 리스크 / 메모

- 메인 백엔드 계약은 바꾸지 않는다. Geometry state 는 최종 PPTX 에만 반영한다.
