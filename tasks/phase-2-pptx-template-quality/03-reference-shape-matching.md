---
id: "2.03"
phase: 2
title: "예시 슬라이드 reference 매칭과 reference.json 생성"
spec: "specs/phase-2/01-pptx-template-v2-compiler.md"
depends_on: ["2.02"]
blocks: ["2.04", "2.05"]
estimate: "M"
status: "done"
completed_at: "2026-06-14"
owner: ""
sprint: ""
---

# Task 2.03 — 예시 슬라이드 reference 매칭과 reference.json 생성

> Spec: [`specs/phase-2/01-pptx-template-v2-compiler.md`](../../specs/phase-2/01-pptx-template-v2-compiler.md)
> GitHub Issue: [#258](https://github.com/Teamie71/folioo-ai/issues/258)

## 의존성

- 2.02 (PPTX slide pair와 marker 추출) — runtime slot 과 example slide pair 가 있어야 위치/크기 기반 매칭을 수행할 수 있다.

## 사전 준비

- [x] bbox center distance, overlap ratio, size similarity 계산 기준 정리
- [x] 예시 텍스트 색상과 줄 수를 검증할 fixture 슬라이드 준비

## 구현 체크리스트

- [x] runtime editable slot 과 example slide text shape 의 후보 score 계산 구현
- [x] `example_text`, `example_char_count`, `example_line_count`, `output_text_color` 추출
- [x] 매칭 실패와 낮은 신뢰도를 fail/warning 으로 분리
- [x] example slide 를 runtime 후보에서 제외한 `runtime_slides` 작성
- [x] `reference.json` 의 `slide_pairs`, `shape_matches` deterministic 생성 테스트 추가

## Definition of Done

- [x] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [x] 예시 슬라이드의 텍스트/줄 수/글자 수/output color 가 reference 로 기록된다
- [x] example slide 가 runtime 후보에 포함되지 않는다
- [x] editable slot 의 예시 shape 매칭 실패가 계약 오류로 보고된다

## 리스크 / 메모

- 예시 슬라이드의 위치/크기/fill/border 는 런타임 스타일로 복사하지 않는다.
