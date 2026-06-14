---
id: "2.12"
phase: 2
title: "relayout_row와 marker color 대체 action"
spec: "specs/phase-2/04-pptx-layout-action-application.md"
depends_on: ["2.11"]
blocks: ["2.13", "2.15", "2.16"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.12 — relayout_row와 marker color 대체 action

> Spec: [`specs/phase-2/04-pptx-layout-action-application.md`](../../specs/phase-2/04-pptx-layout-action-application.md)
> GitHub Issue: [#267](https://github.com/Teamie71/folioo-ai/issues/267)

## 의존성

- 2.11 (SlideEditor resize layout action 적용) — layout action 적용 진입점과 기본 geometry 수정 유틸을 확장한다.

## 사전 준비

- [ ] row item ordering 과 gap 계산을 적용 단계에서 검증할 fixture 준비
- [ ] text replacement 시 run color 를 `output_text_color` 로 바꾸는 위치 확인

## 구현 체크리스트

- [ ] `relayout_row` action 으로 group item 과 linked background 의 x 좌표를 함께 이동
- [ ] group item 순서와 `min_gap_emu` 를 보존하는 검증 추가
- [ ] text fill 적용 시 `#FF0000` marker color 를 `output_text_color` 로 대체
- [ ] `output_text_color` 누락 시 안전 fallback 색상 정책 추가
- [ ] relayout/color replacement 단위 테스트 추가

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] `relayout_row` 후 같은 group item 들의 x 좌표가 순서와 gap 을 만족한다
- [ ] 최종 텍스트에 `#FF0000` marker 색상이 남지 않는다
- [ ] color fallback 사용 시 warning 또는 fit report 에 남는다

## 리스크 / 메모

- text fill 과 style replacement 순서가 꼬이면 marker red 가 결과물에 남을 수 있다.
