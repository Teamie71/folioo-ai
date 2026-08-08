---
id: "3.14"
phase: 3
title: "대상 활동 선택"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.05", "3.13"]
blocks: ["3.15", "3.23"]
estimate: "S"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.14 — 대상 활동 선택

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 5-4, 6-2, 9절 12번
> PR: EM-14 · 브랜치 `feat/{issue}-experience-map-target-activity`

## 의존성

- 3.05 (경험 맵 Repository·alias) — outline 과 alias 소유권 검증
- 3.13 (반영 내용 필터링) — 분류 결과에 따라 대상이 달라진다

## 사전 준비

- [ ] `context_experience_id` 전달 경로와 유효성 판정 기준 확인
- [ ] `ambiguous_target` fallback 문구 확인 (5-11)

## 구현 체크리스트

- [ ] `context_experience_id` 가 유효하면 우선 사용
- [ ] 없으면 사용자 메시지와 outline 으로 선택
- [ ] gap 답변은 `anchor_block_id` 가 속한 활동을 사용
- [ ] **하나로 특정할 수 없으면 commit 없이 fallback(`ambiguous_target`)**
- [ ] 한 요청이 한 활동만 수정하도록 제한
- [ ] 읽기는 outline 전체 + 상세 1개, 쓰기는 한 활동만 (6-2)

## Definition of Done

- [ ] 우선순위 3단계(`context_experience_id` → 메시지+outline → `anchor_block_id`)가 동작한다
- [ ] 대상이 불명확하면 DB 변경 없이 fallback 으로 되묻는다
- [ ] 다른 사용자·다른 활동 alias 사용이 차단된다
- [ ] 한 요청이 두 활동을 수정하지 않는다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- 작지만 독립시킨 이유는 fallback 되묻기와 alias 소유권이 함께 걸려 있기 때문이다.
- **빈칸은 되돌릴 수 있지만 잘못 배정된 내용은 사용자가 찾지 못한다.** 임의 배정을 절대 허용하지 않는다.
