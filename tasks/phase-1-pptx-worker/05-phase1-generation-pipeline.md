---
id: "1.05"
phase: 1
title: "Phase 1 초기 생성 파이프라인 오케스트레이션 (Step 1~7)"
spec: "specs/phase-1/05-phase1-generation-pipeline.md"
depends_on: ["1.02", "1.03", "1.04", "1.06", "1.11", "1.12"]
blocks: ["1.07", "1.09"]
estimate: "L"
status: "todo"
owner: ""
sprint: ""
---

# Task 1.05 — Phase 1 초기 생성 파이프라인 오케스트레이션 (Step 1~7)

> Spec: [`specs/phase-1/05-phase1-generation-pipeline.md`](../../specs/phase-1/05-phase1-generation-pipeline.md)

## 의존성

- 1.03 (SlideEditor) — Step 3 `extract_slots`→`apply_fills`
- 1.04 (soffice 렌더) — Step 5 soffice→PDF→pdftoppm
- 1.11 (PPTX 도구 체인) — Step 2·4 unpack/미선택 제거/clean/pack/validate
- 1.12 (GCS 클라이언트) — Step 2·7 템플릿 다운로드 / current.pptx·pdf 업로드
- 1.06 (시각 QA) — Step 6 QA + fix-and-verify + 프리뷰 업로드
- 1.02 (콜백 클라이언트) — 매 단계 진행 이벤트 콜백 + portfolioText 조회

## 사전 준비

- [ ] `features/visualization/agents/`(LangGraph) LLM 노드 골격
- [ ] LLM Call #1/#2 프롬프트(meta.json·썸네일·Slot 디스크립터 입력) 초안

## 구현 체크리스트

- [ ] Step 1: LLM Call #1(rule 사전필터 + LLM 최종선택) → 7~12장 slide_plan + content_brief → `slide-plan` 콜백
- [ ] `slide-plan` 응답에서 메인 DB slide id 목록을 받아 `slide_order -> slide_id` 매핑 구성(204 응답은 초기 생성 파이프라인 오류)
- [ ] Step 2~3: 선택 슬라이드만 남긴 작업파일에서 슬라이드별 병렬 `extract_slots`→Call #2→`apply_fills` → `slide_content_ready`
- [ ] Step 3 실패 슬라이드는 템플릿 원문이 보이지 않도록 완전 빈 페이지로 비우고 deck 에는 남겨 slideOrder/page 매핑 유지
- [ ] Step 4~5: 전체 1회 pack/validate → `pipeline_stage_changed: rendering` → soffice→PDF→pdftoppm
- [ ] Step 7: current.pptx/pdf GCS 업로드 → `all_completed`(gcsPptxKey·summary{completed,failed}·전체실패 errorCode)
- [ ] portfolioText 는 메인 internal API 조회 (payload 미포함), LLM 은 데이터(fills)만 산출
- [ ] Step 3 Call #2 실패: 타임아웃 자동 1회 재시도 → 실패 시 `slide_content_error`(slideOrder·message), 해당 슬라이드 error + summary.failed 반영(§12.2/§13)
- [ ] 이벤트 idempotency key 는 payload key 재사용이 아닌 이벤트 단위 안정 키로 생성
- [ ] 텍스트 적응(§16): 폰트 축소 60%/하한 10pt, 요약

## Definition of Done

- [ ] cover/closing 포함·7~12장·연속 카테고리 회피 slide_plan 검증
- [ ] 슬라이드별 Call #2 병렬 + `slide_content_ready` 도착순 발신 검증
- [ ] pack→soffice→pdftoppm 각 1회 + `pipeline_stage_changed: rendering` 1회 검증
- [ ] 전체성공/일부실패/전체실패 `all_completed` summary·errorCode 검증
- [ ] LLM 1회 재시도 후 실패 시 `slide_content_error` 발신 + 나머지 슬라이드 계속 진행 검증

## 리스크 / 메모

- Step 5/6 분리 이유: soffice 는 파일 1회 변환 효율적, QA 는 슬라이드별 병렬 이득 큼.
- 1.01 핸들러가 본 오케스트레이션을 위임 호출 — 인터페이스 합의 필요.
