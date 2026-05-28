---
id: "1.07"
phase: 1
title: "Phase 2 재생성/재시도 파이프라인 (단일 슬라이드)"
spec: "specs/phase-1/07-phase2-regenerate-retry-pipeline.md"
depends_on: ["1.05"]
blocks: []
estimate: "M"
status: "done"
completed_at: "2026-05-28"
owner: ""
sprint: ""
---

# Task 1.07 — Phase 2 재생성/재시도 파이프라인 (단일 슬라이드)

> Spec: [`specs/phase-1/07-phase2-regenerate-retry-pipeline.md`](../../specs/phase-1/07-phase2-regenerate-retry-pipeline.md)

## 의존성

- 1.05 (Phase 1 파이프라인) — 편집·패키징·렌더·QA·GCS 컴포넌트(03·11·04·06·12 조합)를 재사용하고 머리(④ 새 내용 계산)만 `isRetry` 로 분기한다. 05 가 그 컴포넌트 조립을 완료해야 단일 슬라이드 경로를 얹을 수 있다. (콜백 클라이언트 02·컨텍스트 조회는 05 경유로 전이적 확보)

## 사전 준비

- [x] Phase 2 수정 해석 LLM 프롬프트(§5.3·§5.4.1 가드) 초안
- [x] `current.pptx` 가 존재하는 partial_error Job 픽스처

## 구현 체크리스트

- [x] 컨텍스트 조회(currentFills·sourceSlideId) → `jobs/{job_id}/current.pptx` GCS 다운로드 → unpack → 대상 슬라이드 XML 만 수정
- [x] `isRetry=false`: `userRequest` LLM 해석(폰트 10~48pt, 슬라이드 밖 금지, 미지정 도형 불변, 명시 시에만 텍스트 변경)
- [x] `isRetry=true`: `userRequest` 없이 `jobs.slide_plan` content_brief 로 Phase 1 Step 3 로직 재사용
- [x] 꼬리 공통: pack(`--original current.pptx`)→해당 페이지만 soffice/pdftoppm→시각 QA(이슈 시 2회)→current.pptx/pdf 덮어쓰기 + 새 프리뷰 PUT → `slide_regenerated`(currentFills·gcsPreviewKey)
- [x] 실패 시 `slide_preview_error` 만 발신 (상태 롤백/카운터 보상은 메인 책임 — 워커 미관여)
- [x] 워커 가드: slide 상태 `regenerating`(재생성)/`generating`(재시도)일 때만 처리

## Definition of Done

- [x] `isRetry=false` "제목 크기 키워줘" 시 지정 도형만 변경·미지정 불변 검증
- [x] `isRetry=true` 시 userRequest 없이 content_brief 로 재충전 검증
- [x] current.pptx/pdf 덮어쓰기 + 해당 페이지 프리뷰만 갱신 + `slide_regenerated` 발신 검증
- [x] 상태가 regenerating/generating 아닐 때 push 오면 처리 안 하고 200 skip 검증

## 리스크 / 메모

- 동시성·한도·`current.pptx` 덮어쓰기 직렬화(Job row lock)는 전부 메인 백엔드 책임 — 워커는 push 신뢰 + 멱등 처리만.
