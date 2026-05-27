# 시각 QA + Fix-and-Verify 루프 + 프리뷰 업로드

## Purpose
렌더된 슬라이드 이미지를 LLM 비전으로 검사하고, 이슈가 있으면 자동 수정·재렌더·재검사하며, 통과한 슬라이드부터 GCS 업로드 후 메인에 콜백하는 QA 단계(Step 6)를 구현한다.

## Requirements
- `VisualQA.check_slide()` 가 프리뷰 이미지를 LLM 비전에 넘겨 오버플로우/잘림·요소 겹침·미교체 안내문구·가독성·레이아웃 균형을 검사하고 통과/이슈 목록을 반환한다.
- 통과 슬라이드는 GCS `jobs/{job_id}/previews/slide-{slide_order:02d}.jpg` 에 PUT 후 `slide_preview_ready`(gcsPreviewKey·width·height·byteSize) 콜백을 슬라이드별로 즉시 발신한다.
- 이슈 슬라이드는 fix-and-verify 큐에 적재해 LLM 지시대로 XML 일괄 수정→pack 1회·PDF 변환 1회(배치)→영향 슬라이드만 재 QA 한다.
- 최대 2회 시도에도 실패하면 해당 슬라이드 `slide_preview_error`(message·retryable) 콜백을 보낸다.
- 렌더→시각 QA 검증을 최소 한 번 거치기 전에는 완료를 선언하지 않고, 재검증은 영향 받은 슬라이드만 검사한다(Anthropic 스킬 원칙).

## Approach
`apps/pptx-worker/features/visualization/` 에 `VisualQA` 와 fix-and-verify 컨트롤러를 두고 spec 04 의 soffice/pdftoppm·GCS, spec 03 의 SlideEditor 를 재사용한다. 통과 슬라이드를 즉시 푸시해 "빠른 슬라이드부터 검토 가능"한 UX 를 만들고(§2.2), 자동 수정은 재생성 한도를 차감하지 않는다(§14). QA 입력 컨텍스트는 content_brief + fills 요약이며, 자동 요약 시 숫자·고유명사·성과 지표는 보존하도록 프롬프트로 가드한다(qa-and-guardrails.md §5.4.1). 워커는 signed URL 을 발급하지 않고 gcsPreviewKey 만 콜백한다.

## Verification
- 오버플로우/미교체 안내문구가 있는 슬라이드 이미지를 QA 가 이슈로 분류하고, 정상 이미지는 통과로 분류하는지 검증한다.
- 통과 슬라이드가 canonical key 로 GCS 업로드되고 `slide_preview_ready` 콜백이 슬라이드별로 발신되는지 확인한다.
- 이슈 슬라이드가 1차 수정 후 통과하면 업로드되고, 2회 실패 시 `slide_preview_error`(retryable 포함) 콜백이 발신되는지 검증한다.
- fix-and-verify 가 영향 슬라이드만 재 QA 하고 pack/PDF 변환을 배치 1회로 묶는지 확인한다.
