# Phase 2 재생성/재시도 파이프라인 (단일 슬라이드)

## Purpose
regenerate push 를 받아 단일 Slide 를 사용자 요청(재생성) 또는 저장된 content_brief(재시도)로 다시 채우고, 재렌더·재 QA 후 current.pptx/current.pdf/프리뷰를 갱신해 결과를 콜백하는 파이프라인을 구현한다.

## Requirements
- 메인 컨텍스트 조회(currentFills·sourceSlideId) 후 GCS `jobs/{job_id}/current.pptx` 다운로드 → unpack → 대상 슬라이드 XML 만 수정한다.
- `isRetry=false`: `userRequest` 를 LLM 으로 해석해 변경 지시를 만든다(폰트 10~48pt, 요소 슬라이드 밖 금지, 미지정 도형 불변, 사용자 명시 시에만 텍스트 변경).
- `isRetry=true`: `userRequest` 없이 jobs.slide_plan 의 해당 슬라이드 `content_brief` 로 Phase 1 Step 3 로직을 재사용해 채운다.
- 꼬리 공통: pack(`--original current.pptx`) → 해당 페이지만 soffice/pdftoppm 렌더 → 시각 QA(이슈 시 최대 2회 수정) → current.pptx/current.pdf 덮어쓰기 + 새 프리뷰 PUT 후 `slide_regenerated`(currentFills·gcsPreviewKey) 콜백.
- 실패 시 `slide_preview_error` 콜백만 보내고, 상태 롤백/카운터 보상은 메인이 처리하므로 워커는 관여하지 않는다.

## Approach
`apps/pptx-worker/features/visualization/service.py` 에 재생성 핸들러를 두되 spec 05 의 편집·렌더·QA 컴포넌트를 재사용하고 머리(④ 새 내용 계산)만 `isRetry` 로 분기한다(§5.3). 동시성 제어·재생성 한도·`current.pptx` 덮어쓰기 직렬화(Job row lock)는 모두 메인 백엔드 책임이므로 워커는 push 메시지를 신뢰해 멱등 처리만 한다(§7.4). 워커 가드는 slide 상태가 `regenerating`(재생성) 또는 `generating`(재시도)일 때만 처리한다(§7.4.5).

## Verification
- `isRetry=false` 로 "제목 크기 키워줘" 요청 시 지정 도형만 변경되고 미지정 도형은 그대로인지 검증한다.
- `isRetry=true` 시 `userRequest` 없이 content_brief 로 슬라이드가 다시 채워지는지 확인한다.
- 재생성 후 current.pptx/current.pdf 가 덮어써지고 해당 페이지 프리뷰만 갱신되며 `slide_regenerated` 콜백이 발신되는지 확인한다.
- slide 상태가 `regenerating`/`generating` 이 아닐 때 push 가 와도 워커가 처리하지 않고 200 으로 skip 하는지 검증한다.
