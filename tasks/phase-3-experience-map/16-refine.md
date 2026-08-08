---
id: "3.16"
phase: 3
title: "문장 정제 노드"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.15"]
blocks: ["3.17"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.16 — 문장 정제 노드

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 2-1, 5-6, 9절 14번
> PR: EM-16 · 브랜치 `feat/{issue}-experience-map-refine`
> GitHub Issue: [#308](https://github.com/Teamie71/folioo-ai/issues/308)

## 의존성

- 3.15 (블록 구조화) — 구조화 결과를 입력으로 받는다.

## 사전 준비

- [ ] 2-1 좋은 경험정리의 특징 3가지를 프롬프트 반영 형태로 정리
- [ ] gap `extend_block` 결합 시 기존 블록 텍스트 조회 경로 확인

## 구현 체크리스트

- [ ] `nodes/refine.py` + `prompts/refine.py`
- [ ] 구조화 결과: 블록과 원문의 매핑을 유지한 채 원문만 정제
- [ ] gap 답변(`extend_block`): **`anchor_block_id` 의 기존 텍스트와 답변을 결합**해 정제 후 update
- [ ] 정제 기준 — What/How/Why/Result, **명사 종결**, 화살표(`→`)·슬래시(`/`) 구조적 표기
- [ ] **한 활동 단위로 묶어 1회 호출** (블록별 분할 호출 아님)
- [ ] 출력 스키마에서 배정 필드 제거
- [ ] 입력 item 집합 == 출력 item 집합 검증

## Definition of Done

- [ ] 정제 전후 operation metadata 가 동일하다
- [ ] 입력 item 집합과 출력 item 집합이 일치한다
- [ ] 원문 근거가 없는 수치·고유명사 생성이 차단된다
- [ ] gap 결합 시 기존 내용이 유실되지 않는다
- [ ] 명사 종결과 구조적 표기가 적용된다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **배정은 바꿀 수 없다.** 출력 스키마에서 배정 필드를 제거해 구조적으로 막는다. 프롬프트 지시만으로 막지 않는다.
- 정제는 hallucination 위험이 가장 높은 노드다. "텍스트에 없는 내용의 임의 생성 절대 금지"를 프롬프트 최상단에 둔다.
