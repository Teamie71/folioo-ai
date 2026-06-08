# 메인 백엔드 콜백/컨텍스트 클라이언트 (워커 측)

## Purpose
시각화 워커가 메인 백엔드의 `/internal/visualizations/...` 엔드포인트로 진행 이벤트를 콜백하고 작업 컨텍스트(포트폴리오 텍스트·slide_plan·slide 상태)를 조회하는 HTTP 클라이언트를 구축한다.

## Requirements
- slide-plan 제출, slide 레벨 이벤트(`slide_content_ready`/`slide_preview_ready`/`slide_*_error`/`slide_regenerated`), job 레벨 이벤트(`pipeline_stage_changed`/`all_completed`) 콜백 메서드를 제공한다.
- 컨텍스트 조회 메서드(`GET /{job_id}` 로 portfolioText·slidePlan, `GET /{job_id}/slides/{slide_id}` 로 currentFills·sourceSlideId)를 제공한다.
- camelCase(API) ↔ snake_case(워커 내부) 필드 매핑을 적용하되, `slidePlan`/`currentFills` JSONB blob 내부 키는 snake_case 그대로 유지한다(§11.0.3).
- 콜백은 멱등 보장 전제이며 5xx/timeout 시 선형 백오프+jitter 로 최대 5회 재시도한다(§11.0.4).
- 워커는 DB 에 직접 연결하지 않으며 모든 상태 read/write 는 이 클라이언트만 경유한다.

## Approach
`apps/pptx-worker/features/visualization/` 에 클라이언트를 두되, 기존 `common/http_client/`(`request_with_retry`, `_parse_envelope`, `MainServerError`)와 `common/clients/base_client.py`(X-API-Key 첨부) 패턴을 그대로 재사용한다. 응답 envelope `{ isSuccess, result }` 파싱과 camel→snake 매핑은 `common/main_server/portfolio_client.py` 의 `FIELD_MAP_*` 방식을 따른다(§10.0 매핑 표). 인증은 `MAIN_BACKEND_URL`/`MAIN_BACKEND_API_KEY` 환경변수 기반 `X-API-Key` 헤더다.

## Verification
- 각 콜백 메서드가 올바른 경로·바디(camelCase, JSONB 내부는 snake_case)로 POST 하는지 mock 으로 검증한다.
- `GET /{job_id}` 응답의 `portfolioText`/`slidePlan` 과 slide 응답의 `currentFills` 가 워커 내부 snake_case 구조로 매핑되는지 확인한다.
- 메인이 5xx 를 반환할 때 최대 5회 재시도 후 `MainServerError` 를 던지는지 검증한다.
- 4xx 응답은 재시도 없이 즉시 실패로 전파되는지 확인한다.
