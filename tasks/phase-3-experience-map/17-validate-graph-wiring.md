---
id: "3.17"
phase: 3
title: "validate 노드와 보정 loop, graph 배선"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.15", "3.16"]
blocks: ["3.18", "3.19"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.17 — validate 노드와 보정 loop, graph 배선

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 4절, 5-7, 7-2, 9절 15번
> PR: EM-17 · 브랜치 `feat/{issue}-experience-map-validate-graph`

## 의존성

- 3.15 (블록 구조화), 3.16 (문장 정제) — 회귀 분기 대상 노드

## 사전 준비

- [ ] 4절 파이프라인 다이어그램과 회귀 분기 조건 확인
- [ ] 2-3 위계별 AI 권한표 확인

## 구현 체크리스트

- [ ] `nodes/validate.py` — 검증 항목
  - [ ] content 가 있으면 1~500자 (템플릿 빈 슬롯·카테고리 컨테이너는 빈 값 허용)
  - [ ] alias 존재와 사용자·활동 소유권
  - [ ] 부모·target·after 존재, 같은 부모의 형제 여부
  - [ ] `parent_ref` 와 `parent_item_id` 동시 지정 금지 (`add` 는 둘 중 하나 필수)
  - [ ] 부모와 생성 블록의 위계, **5단계 초과 금지**
  - [ ] 위계별 AI 권한 (1·2단계 생성 금지, 3단계 수정 금지, 전 위계 삭제 금지)
  - [ ] `is_text_editable` 여부, item 누락·중복
  - [ ] 입력 사실 보존과 hallucination 금지
- [ ] 회귀 분기 — 위계·권한 위반 → structure / 글자수 위반 → refine
- [ ] 보정 **최대 2회**, 초과 항목은 커밋 items 에서 제외하고 `dropped` 에 담음
- [ ] `graph.py` — 전체 분기 연결, PostgreSQL checkpointer 로 compile
- [ ] 자동 재시도 대상 노드에 `RetryPolicy(max_attempts=2)`
- [ ] **gap 분석·제안·커밋에는 RetryPolicy 미적용**
- [ ] Fallback 과 validate 성공 경로는 graph 를 종료하고 coordinator 로 넘김

## Definition of Done

- [ ] 모든 graph 분기가 테스트로 커버된다
- [ ] 보정 loop 가 2회에서 멈추고 무한 루프하지 않는다
- [ ] 항목 하나가 탈락해도 나머지 정상 블록은 살아남는다
- [ ] 위계별 AI 권한 위반이 전부 차단된다
- [ ] LLM client 의 `max_retries` 가 0 이라 중복 retry 가 없다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- 여기서 그래프가 처음으로 전부 연결된다. 분기 테이블을 테스트로 전수 커버하는 것이 이 태스크의 비용 대부분이다.
- 최종 검증은 메인 서버가 커밋 API 에서 수행하지만, AI 도 여기서 사전 차단한다. 이중 방어를 줄이지 않는다.
