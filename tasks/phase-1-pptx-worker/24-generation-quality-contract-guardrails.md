---
id: "1.24"
phase: 1
title: "생성 품질 계약 보강: Slot 필터링, Chart/Reason 보존, Phase 2 가드레일"
spec: "specs/phase-1/05-phase1-generation-pipeline.md"
depends_on: ["1.03", "1.05", "1.06", "1.07"]
blocks: ["1.26"]
estimate: "M"
status: "done"
completed_at: "2026-05-28"
owner: ""
sprint: ""
---

# Task 1.24 — 생성 품질 계약 보강: Slot 필터링, Chart/Reason 보존, Phase 2 가드레일

> Spec: [`specs/phase-1/05-phase1-generation-pipeline.md`](../../specs/phase-1/05-phase1-generation-pipeline.md)
> GitHub Issue: [#239](https://github.com/Teamie71/folioo-ai/issues/239)

## 의존성

- 1.03 (OOXML 슬라이드 편집 엔진) — decorative/non-editable slot 추출 기준과 fill 적용 계약을 보강한다.
- 1.05 (Phase 1 초기 생성 파이프라인 오케스트레이션) — slide selection, Call #2 fill 생성, reason 보존 경로를 보강한다.
- 1.06 (시각 QA + Fix-and-Verify 루프 + 프리뷰 업로드) — QA fix prompt의 숫자/고유명사/성과 지표 보존 지침과 일관성이 필요하다.
- 1.07 (Phase 2 재생성/재시도 파이프라인) — 사용자 수정 요청 가드레일과 미지정 도형 불변 원칙을 보강한다.

## 사전 준비

- [x] GitHub Issue #239 본문과 현재 `extract_slots()` / fill 생성 prompt / Phase 2 parser 동작 확인
- [x] 스펙상 허용된 수정 범위와 현재 구현 가능 범위 비교
- [x] chart/numeric slide selection 회귀를 재현할 수 있는 fixture 확인

## 구현 체크리스트

- [x] 텍스트가 없는 장식 도형, 배경/레이아웃 보조 도형, 실제 편집 가능한 텍스트 도형 구분 기준 확인
- [x] `extract_slots()`가 LLM에 전달할 slot descriptor에서 비편집 도형을 제외하거나 optional/decorative로 표시하도록 개선
- [x] chart slot과 text slot의 필수/선택 채움 규칙 분리
- [x] fill 생성 단계가 장식 도형을 채우지 않았다는 이유로 실패하지 않도록 계약 정리
- [x] 숫자/성과 지표가 있는 포트폴리오에서 chart 또는 metric-oriented slide 후보가 포함되는지 테스트
- [x] template category/description/best_for 기반 filtering이 의도대로 작동하는지 테스트
- [x] 선택된 slide의 `reason` 또는 선택 근거 필드가 pipeline 후속 단계까지 보존되는지 테스트
- [x] cover/closing 포함, 7~12장, 연속 카테고리 회피 기존 규칙과 새 테스트의 충돌 확인
- [x] LLM Call #2 입력에서 slot별 역할, 제한, optional 여부가 명확히 표현되는지 확인
- [x] chart fill이 필요한 slide에서 chart data 구조가 누락되지 않도록 테스트 추가
- [x] 숫자, 고유명사, 성과 지표 보존 지침이 fill 생성과 QA fix 양쪽에서 일관되는지 확인
- [x] 지원하지 않는 shape size/color/position/layout 전환 요청이 조용히 잘못 적용되지 않도록 제한 또는 명시적 실패 처리
- [x] "제목만 키워줘" 요청에서 지정 대상 외 텍스트/차트/도형 보존 테스트 추가

## Definition of Done

- [x] decorative/non-editable slot 제외 또는 optional 처리 테스트 통과
- [x] numeric/chart slide selection 테스트 통과
- [x] slide selection reason 보존 테스트 통과
- [x] chart fill 누락 방지 테스트 통과
- [x] Phase 2 지정 대상 외 불변 가드레일 테스트 통과
- [x] `uv run ruff check .` 및 관련 visualization 테스트 통과

## 리스크 / 메모

- 렌더 후 이미지 기반 QA 안정화는 1.23에서 처리한다.
- LLM은 데이터 구조만 산출하고 임의 코드를 실행하지 않는 기존 보안 모델을 유지한다.
