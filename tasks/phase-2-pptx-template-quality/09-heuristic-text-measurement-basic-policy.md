---
id: "2.09"
phase: 2
title: "Heuristic text measurement와 basic_text_area 정책"
spec: "specs/phase-2/03-pptx-deterministic-text-fit-preflight.md"
depends_on: ["2.08"]
blocks: []
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.09 — Heuristic text measurement와 basic_text_area 정책

> Spec: [`specs/phase-2/03-pptx-deterministic-text-fit-preflight.md`](../../specs/phase-2/03-pptx-deterministic-text-fit-preflight.md)

## 의존성

- 2.08 (LLM Slot prompt capacity hint 적용) — 생성 후 preflight 가 prompt 단계의 capacity 정책과 같은 필드를 사용한다.

## 사전 준비

- [ ] 한글/영문/숫자/공백/기호 폭 heuristic 기준 정리
- [ ] 현재 텍스트 축소/요약 정책이 구현된 위치 확인

## 구현 체크리스트

- [ ] 문자군별 heuristic text width 측정 유틸 구현
- [ ] 10~15% safety margin, padding, `max_lines`, `nowrap` 검사 추가
- [ ] `basic_text_area` fit policy 의 요약, `min_font_pt` 제한, 실패 판정 모델 구현
- [ ] 8pt 이하 shrink 금지 테스트 추가
- [ ] fit 결과와 실패 사유 structured log 출력 추가

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] 한글/영문/숫자 혼합 텍스트의 예상 폭이 deterministic 하게 계산된다
- [ ] `basic_text_area` 는 `min_font_pt` 아래로 줄이지 않는다
- [ ] overflow 가 숨겨지지 않고 요약 또는 실패로 수렴한다

## 리스크 / 메모

- 실제 font metric 이 아니라 heuristic 이므로 최종 렌더 문제 감지는 Visual QA 에 남긴다.
