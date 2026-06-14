---
id: "2.02"
phase: 2
title: "PPTX slide pair와 #FF0000 marker 추출"
spec: "specs/phase-2/01-pptx-template-v2-compiler.md"
depends_on: ["1.03", "2.01"]
blocks: ["2.03"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.02 — PPTX slide pair와 #FF0000 marker 추출

> Spec: [`specs/phase-2/01-pptx-template-v2-compiler.md`](../../specs/phase-2/01-pptx-template-v2-compiler.md)
> GitHub Issue: [#257](https://github.com/Teamie71/folioo-ai/issues/257)

## 의존성

- 1.03 (SlideEditor) — OOXML shape id, bbox, text run 서식 추출 패턴을 재사용한다.
- 2.01 (v2 메타데이터 모델) — 추출 결과를 v2 slot skeleton 에 기록한다.

## 사전 준비

- [ ] `docs/architecture/pptx-template-quality-roadmap.md` 의 짝수/홀수 convention 확인
- [ ] 샘플 PPTX에서 빨간 marker, non-red text, mixed run fixture 후보 확보

## 구현 체크리스트

- [ ] PPTX package 에서 slide XML 순서와 runtime/example pair 를 계산하는 추출기 추가
- [ ] 텍스트 shape 의 run color 를 정확한 RGB `#FF0000` 기준으로 판별
- [ ] `#FF0000` marker shape 만 editable slot 으로 만들고 non-red text 는 제외
- [ ] red/non-red mixed run 을 계약 위반으로 수집하는 오류 모델 추가
- [ ] marker slot 에 shape id, bbox, font size, `marker_color`, `placeholder_text` 를 기록

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] 유형 슬라이드의 빨간 텍스트만 editable slot 으로 추출된다
- [ ] non-red 텍스트는 slot 에 들어가지 않는다
- [ ] mixed-color run fixture 가 fail 대상 오류로 보고된다

## 리스크 / 메모

- theme red, tint/shade red, `#FE0000` 은 editable marker 로 보지 않는다.
