---
id: "3.05"
phase: 3
title: "경험 맵 Repository 와 별칭 화이트리스트"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.01", "3.02"]
blocks: ["3.14", "3.15"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.05 — 경험 맵 Repository 와 별칭 화이트리스트

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 6-1, 9절 5번
> PR: EM-05 · 브랜치 `feat/{issue}-experience-map-map-repo`

## 의존성

- 3.01 (DB 연결 분리), 3.02 (스키마·오류 모델)
- **외부-A**: 메인 DB migration (`experience_map`, `block.placeholder` 컬럼)

## 사전 준비

- [ ] `block` 테이블의 level·kind·placeholder 컬럼 정의 확보
- [ ] AI 서버 DB 계정이 `block`·`block_kind`·`experience_map` 에 **SELECT 만** 가능한지 확인

## 구현 체크리스트

- [ ] `get_map(user_id)` — **읽기 전용**
- [ ] flat block 목록을 `position` 정렬된 tree 로 변환
- [ ] 그룹·활동 outline 생성
- [ ] 선택 활동 full context 생성 (2~5단계 전체 블록)
- [ ] 실제 ID ↔ alias 양방향 변환 (`exp_*`, `b_*`)
- [ ] 들여쓰기 트리 텍스트 렌더링 (JSON 아님)
- [ ] 빈 블록을 `(빈 블록 — 가이드: …)` 로 표시
- [ ] map version 조회

## Definition of Done

- [ ] 매핑에 없는 alias 는 해당 항목만 탈락시키고 나머지는 살린다
- [ ] 다른 사용자·다른 활동의 alias 가 역변환되지 않는다
- [ ] placeholder 와 사용자 작성 내용이 렌더링에서 구분된다
- [ ] 같은 맵에 대해 alias 부여가 한 요청 안에서 deterministic 하다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **배정 오류 방어의 1차 방어선**이다 (통합 문서 6절). 블록 ID(`bigint`)를 LLM 에 절대 노출하지 않는다 — 원본 ID 를 주면 LLM 이 그럴듯한 숫자를 지어낸다.
- 트리 텍스트 렌더링은 토큰을 절반 이하로 줄이는 목적도 있다. JSON 으로 되돌리지 않는다.
