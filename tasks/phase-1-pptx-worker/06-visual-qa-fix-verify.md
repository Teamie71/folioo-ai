---
id: "1.06"
phase: 1
title: "시각 QA + Fix-and-Verify 루프 + 프리뷰 업로드"
spec: "specs/phase-1/06-visual-qa-fix-verify.md"
depends_on: ["1.02", "1.03", "1.04"]
blocks: ["1.05"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 1.06 — 시각 QA + Fix-and-Verify 루프 + 프리뷰 업로드

> Spec: [`specs/phase-1/06-visual-qa-fix-verify.md`](../../specs/phase-1/06-visual-qa-fix-verify.md)

## 의존성

- 1.04 (샌드박스/렌더/GCS) — soffice/pdftoppm 재렌더와 프리뷰 GCS PUT 에 사용
- 1.03 (SlideEditor) — fix-and-verify 의 XML 일괄 수정에 사용
- 1.02 (콜백 클라이언트) — `slide_preview_ready`/`slide_preview_error` 콜백 발신에 사용

## 사전 준비

- [ ] LLM 비전 호출 클라이언트(`common/llm`) 확인
- [ ] 오버플로우/미교체 안내문구 포함 샘플 프리뷰 이미지 픽스처

## 구현 체크리스트

- [ ] `VisualQA.check_slide()` — 오버플로우/잘림·겹침·미교체 안내문구·가독성·균형 검사 → 통과/이슈 목록
- [ ] 통과 슬라이드: canonical key `previews/slide-{slide_order:02d}.jpg` PUT 후 `slide_preview_ready`(gcsPreviewKey·width·height·byteSize) 즉시 발신
- [ ] 이슈 슬라이드: fix-and-verify 큐 → XML 일괄 수정 → pack 1회·PDF 변환 1회(배치) → 영향 슬라이드만 재 QA
- [ ] 최대 2회 실패 시 `slide_preview_error`(message·retryable) 발신
- [ ] 완료 전 최소 1회 시각 QA 강제, 재검증은 영향 슬라이드만
- [ ] 자동 요약 가드: 숫자·고유명사·성과 지표 보존 프롬프트(§5.4.1), 자동 수정은 한도 미차감

## Definition of Done

- [ ] 이슈/정상 이미지 분류 정확도 검증
- [ ] 통과 슬라이드 canonical key 업로드 + 슬라이드별 `slide_preview_ready` 발신 검증
- [ ] 1차 수정 통과 시 업로드, 2회 실패 시 `slide_preview_error`(retryable) 검증
- [ ] fix-and-verify 가 영향 슬라이드만 재 QA, pack/PDF 배치 1회 검증

## 리스크 / 메모

- 워커는 signed URL 발급 안 함 — gcsPreviewKey 만 콜백(메인이 signed 변환).
- "빠른 슬라이드부터 즉시 푸시" UX(§2.2) 위해 통과 즉시 발신.
