---
id: "3.02"
phase: 3
title: "경험정리 feature 스캐폴드와 스키마·오류 모델"
spec: "docs/architecture/experience-map-agent.md"
depends_on: []
blocks: ["3.03", "3.04", "3.05", "3.06", "3.07", "3.08", "3.09"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.02 — 경험정리 feature 스캐폴드와 스키마·오류 모델

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 8-1, 9절 3번
> API: [`docs/architecture/experience-map-api-spec.md`](../../docs/architecture/experience-map-api-spec.md) 4-2, 6절
> PR: EM-02 · 브랜치 `feat/{issue}-experience-map-schemas`
> GitHub Issue: [#301](https://github.com/Teamie71/folioo-ai/issues/301)

## 의존성

- 없음. 이후 21개 태스크가 여기서 정의한 모델을 계약으로 쓴다.

## 사전 준비

- [x] API 명세 4-2 커밋 items 필드표와 6절 SSE 이벤트 예시 JSON 확보
- [x] 통합 문서 8-1 structured output 모델 확인
- [x] 기존 `app/schemas/` 네이밍·검증 관례 확인

## 구현 체크리스트

- [x] `features/experience_map/` 패키지 생성 (`__init__.py` 명시적 익스포트)
- [x] `config.py` — 환경변수 설정 모델, 노드별 timeout, 경험정리 LLM client 내장 retry 0
- [x] `schemas.py` — `RouterOutput`, `FilteredItem`, `ContentFilterOutput`, `StructuredItem`, `RefinedItem`, `GapOutput`
- [x] add·update operation 모델 (`section_kind`·`slot_id`·`after_id` 포함)
- [x] `active_gap` 모델 (`gap_id`·`gap_type`·`anchor_block_id`)
- [x] `app/schemas/experience_map.py` — API request/response, SSE event 모델
- [x] `errors.py` — feature exception 과 HTTP/SSE 오류 매핑, 공통 API key·티켓 오류 포맷

## Definition of Done

- [x] UUID·십진 문자열 ID·조건부 필수값 검증이 동작한다
- [x] `content` 조건부 필수 검증 (템플릿 빈 슬롯·카테고리 컨테이너는 생략)
- [x] `parent_ref` 와 `parent_item_id` 동시 지정이 스키마에서 거부된다
- [x] `RefinedItem` 에 배정 필드가 존재하지 않는다
- [x] API 명세 예시 JSON 과 직렬화 결과가 일치한다
- [x] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- LLM 호출도 DB 접근도 넣지 않는다. 순수 모델 PR 로 유지해야 리뷰가 계약 검토에 집중된다.
- `slot_id` 는 `str` 이므로 목록(10절 1번) 미확정 상태에서도 이 태스크는 완결된다.
- 결과: `1058 passed` (기존 982 + 신규 76). ruff check·format 통과.
- `GapOutput.gap` 은 별칭 기준 `GapCandidate` 로 정의했다. LLM 은 실제 block ID 를 보지 못하므로
  `anchor_ref`(별칭)를 내고, 저장용 `ActiveGap` 의 `anchor_block_id` 로는 AI 서버가 변환한다.
