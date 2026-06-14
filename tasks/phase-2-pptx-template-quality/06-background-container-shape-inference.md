---
id: "2.06"
phase: 2
title: "item_background와 container_shape 추론"
spec: "specs/phase-2/02-pptx-slot-layout-group-inference.md"
depends_on: ["2.05"]
blocks: ["2.07"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.06 — item_background와 container_shape 추론

> Spec: [`specs/phase-2/02-pptx-slot-layout-group-inference.md`](../../specs/phase-2/02-pptx-slot-layout-group-inference.md)
> GitHub Issue: [#261](https://github.com/Teamie71/folioo-ai/issues/261)

## 의존성

- 2.05 (Slot capacity 계약 확장) — text slot 의 bbox 와 capacity 정보가 준비되어야 배경/컨테이너 후보를 분류할 수 있다.

## 사전 준비

- [ ] 작은 chip background 와 큰 카드 background fixture 구분
- [ ] shape bbox overlap, containment, center distance 계산 유틸 위치 결정

## 구현 체크리스트

- [ ] 텍스트 없는 shape 중 text slot 을 감싸는 작은 후보를 `item_background` 로 score 계산
- [ ] 동일 background 가 여러 slot 과 겹치는 ambiguous case 를 warning/fallback 으로 분리
- [ ] 여러 text slot 을 담는 큰 shape 를 `container_shape` 로 분류
- [ ] `container_shape` 는 참고 정보로만 기록하고 개별 resize 대상에서 제외
- [ ] background/container 추론 단위 테스트와 origin fixture 기반 회귀 테스트 추가

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] chip text slot 이 1:1 `item_background` 와 연결된다
- [ ] 큰 카드 배경은 `container_shape` 로 기록되고 resize linked 대상이 아니다
- [ ] ambiguous background 후보는 억지로 연결되지 않고 warning 으로 남는다

## 리스크 / 메모

- 큰 컨테이너 내부 재배치는 후속 범위다. 이번 task 는 감지와 분류만 다룬다.
