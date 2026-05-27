# Phase 1 초기 생성 파이프라인 오케스트레이션 (Step 1~7)

## Purpose
generate push 한 건을 받아 LLM 구조 분석·Slot Fill 결정·편집·렌더·QA·업로드(Step 1~7)를 순서대로 실행하고 매 단계 진행 이벤트를 메인에 콜백하는 오케스트레이션을 구현한다.

## Requirements
- Step 1: LLM Call #1(구조 분석 + Source Slide 선택, rule-based 사전 필터 후 LLM 최종 선택)로 7~12장 slide_plan 과 슬라이드별 `content_brief` 를 생성하고 `slide-plan` 콜백을 보낸다.
- `slide-plan` 콜백 응답은 메인이 생성/조회한 슬라이드 행 목록(`id`, `slideOrder`, `sourceSlideId`, `slideFilename`)을 반환해야 하며, 워커는 이 `slide_id` 매핑으로 이후 모든 slide event 를 보낸다. 기존 204 응답은 초기 생성 파이프라인 계약에서는 오류로 본다.
- Step 2~3: 선택 슬라이드만 남긴 작업 파일에서 슬라이드별 병렬로 `extract_slots`→LLM Call #2(Slot→Fill 결정)→`apply_fills` 를 수행하고 슬라이드마다 `slide_content_ready` 콜백을 보낸다.
- Step 3 에서 특정 슬라이드 콘텐츠 생성이 실패하면 해당 슬라이드는 템플릿 원문을 노출하지 않도록 완전 빈 페이지로 비우고 `slide_content_error` 로 분리한다. 실패 슬라이드는 QA/preview 대상에서 제외하되 PPTX 페이지 순서 보존을 위해 deck 에는 남긴다.
- Step 4~5: 전체 1회 pack/validate 후 `pipeline_stage_changed: rendering` 콜백, 이어 soffice→PDF→pdftoppm 으로 페이지별 JPG 를 생성한다.
- Step 7: 성공 슬라이드가 1개 이상이면 current.pptx/current.pdf 를 GCS 에 업로드하고 `all_completed`(gcsPptxKey + summary{completed,failed})를 콜백한다. 전체 실패(`completed=0`)에만 `errorCode` 를 포함한다.
- portfolioText 는 payload 에 없으므로 메인 internal API 로 조회하고, LLM 은 데이터(fills)만 산출하며 임의 코드 실행은 하지 않는다.
- Step 3 슬라이드별 LLM Call #2 실패 시: 타임아웃이면 워커 내부에서 자동 1회 재시도하고, 그래도 실패하면 `slide_content_error`(slideOrder, message)를 발신해 해당 슬라이드를 error 로 두고 `all_completed` 의 summary.failed 에 반영한다(§12.2/§13).
- 진행/최종 이벤트의 idempotency key 는 Cloud Tasks payload 의 key 를 그대로 재사용하지 않고 `{job_id}:slide:{slide_id}:{event}` 또는 `{job_id}:job:{event}:{stage}` 형태의 이벤트 단위 안정 키로 만든다.

## Approach
`apps/pptx-worker/features/visualization/service.py` 가 오케스트레이션을 맡고 LLM 노드는 `features/visualization/agents/`(LangGraph)에 둔다. Phase 1 Call #1 은 meta.json(id/category/description/best_for) 텍스트 메타데이터만 사용하고, 템플릿 썸네일의 multimodal 입력은 모델/비용/응답 지연 정책 확정 뒤 후속 작업으로 둔다. Call #2 는 Slot 디스크립터를 입력으로 사용하며 사전 Slot 스펙은 넣지 않는다(template-system.md §3.7). Step 5/6 분리는 soffice 가 파일 1회 변환이 효율적인 반면 QA 는 슬라이드별 병렬 이득이 크기 때문이다(§5.2). 이 파이프라인은 spec 03(SlideEditor)·11(PPTX 도구 체인)·04(soffice 렌더)·12(GCS 클라이언트)·06(시각 QA)·02(콜백 클라이언트)를 조합하며, 텍스트 길이 적응(폰트 축소 60%/하한 10pt, 요약)은 §16 정책을 따른다.

## Verification
- 포트폴리오 텍스트를 입력하면 cover/closing 포함·7~12장 범위·연속 카테고리 회피 규칙을 지킨 slide_plan 이 나오는지 검증한다.
- 슬라이드별 LLM Call #2 가 병렬 실행되고 각 슬라이드 완료 시 `slide_content_ready` 콜백이 도착 순서대로 발신되는지 확인한다.
- pack→soffice→pdftoppm 이 1회씩 호출되고 `pipeline_stage_changed: rendering` 이 한 번 발신되는지 확인한다.
- 전체 성공/일부 실패/전체 실패 각각에서 `all_completed` 의 summary·errorCode 가 올바른지 검증한다.
- LLM 1회 재시도 후에도 실패하면 `slide_content_error` 가 발신되고 나머지 슬라이드는 계속 진행되는지 검증한다.
