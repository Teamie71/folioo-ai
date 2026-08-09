---
id: "3.15"
phase: 3
title: "블록 단위 구조화와 템플릿 slot_id 선택"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.03", "3.05", "3.13", "3.14"]
blocks: ["3.16", "3.17"]
estimate: "L"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.15 — 블록 단위 구조화와 템플릿 slot_id 선택

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 2-3, 3절, 5-5, 9절 13번
> PR: EM-15 · 브랜치 `feat/{issue}-experience-map-structure`
> GitHub Issue: [#308](https://github.com/Teamie71/folioo-ai/issues/308)

## 의존성

- 3.03 (템플릿 카탈로그) — `slot_id` 목록과 작성 예시(few-shot)
- 3.05 (경험 맵 Repository·alias), 3.13 (필터링), 3.14 (대상 활동 선택)
- ~~**외부-C**: `slot_id` 38개 목록 확정~~ — **해소됨 (2026-08-09)**, 통합 문서 3-0

## 사전 준비

- [ ] 3-2·3-3·3-4 템플릿 표와 `{SECTION}.{TEMPLATE}.{SLOT}` 명명 규칙 확인
- [ ] 2-3 위계별 AI 권한표 확인
- [ ] fixture 카탈로그 준비 (목록 미확정 대비)

## 구현 체크리스트

- [ ] `nodes/structure.py` + `prompts/structure.py`
- [ ] 새 내용: 3단계 카테고리 판단 → 4단계 항목 → 필요 시 5단계 세부 항목
- [ ] gap 답변(`new_child_block`): `anchor_block_id` 하위로 구조화
- [ ] 필요한 카테고리가 활동에 없으면 카테고리 생성 (`section_kind` 또는 level 3 `CONTENT`)
- [ ] 부모 참조: 기존 블록은 `parent_ref`, 같은 요청 신규 블록은 `parent_item_id` (**동시 지정 금지**)
- [ ] 템플릿 선택 — 3단계 카테고리 / 담당업무 기본 1종 / 문제해결 **6종 중 선택** / 그 외 기본 placeholder
- [ ] **템플릿 사용 시 모든 슬롯 생성**, 정보 없는 슬롯은 `content` 없이 `slot_id` 만 (3-1)
- [ ] 템플릿 미사용 시 값이 들어갈 블록만 생성
- [ ] `level`·`position` 은 출력 스키마에서 제외

## Definition of Done

- [ ] structure 전후 item 집합이 동일하다
- [ ] 입력 텍스트가 그대로 유지된다 (구체성 손실 없음)
- [ ] 1·2단계 생성과 편집 불가 block 수정이 차단된다
- [ ] 이미 있는 카테고리를 중복 생성하지 않는다
- [ ] 생성한 블록마다 올바른 `slot_id` 가 지정된다
- [ ] 문제해결 5단계 템플릿 6종 선택이 내용과 일치한다
- [ ] **템플릿 사용 시 정보가 없는 슬롯도 빈 블록으로 생성된다**
- [ ] 템플릿 미사용 시 빈 블록이 생성되지 않는다
- [ ] 한 요청에서 같은 target 을 중복 update 하지 않는다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- ✅ **`slot_id` 목록이 확정됐다** (2026-08-09). 더 이상 이 태스크의 블로커가 아니다.
- **level 판정은 점 개수로 한다.** 4단계는 2-part(`DETAIL.MOTIVATION`), 5단계는 3-part(`PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE`).
- **앵커 규칙을 검증에 넣는다.** level 5 는 반드시 `TASK.SUMMARY` 또는 `PROBLEM_SOLVING.SUMMARY` 로 만든 level 4 아래에 붙는다. 같은 요청에서 만든 앵커면 `parent_item_id`, 기존 블록이면 `parent_ref` 로 참조한다.
- **하위 템플릿은 담당업무·문제해결만 가진다.** 상세정보·주요성과·배운 점에 level 5 를 만들면 위반이다.
- **반복 가능하다.** 담당업무는 업무 하나당, 문제해결은 에피소드 하나당 한 벌이며 한 활동에 여러 벌이 들어갈 수 있다.
- 완화책: `slot_id` 를 코드 상수로 박지 말고 **3.03 카탈로그에서 받아온 목록으로만** 검증하도록 짜고 fixture 카탈로그로 테스트한다. 목록 확정 후 fixture 만 교체하면 된다.
- **텍스트를 수정하지 않는다.** 정제는 3.16 의 책임이며, 여기서 손대면 구체성이 두 번 깎인다.
