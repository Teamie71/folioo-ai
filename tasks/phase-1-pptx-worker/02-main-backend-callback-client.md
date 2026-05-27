---
id: "1.02"
phase: 1
title: "메인 백엔드 콜백/컨텍스트 클라이언트 (워커 측)"
spec: "specs/phase-1/02-main-backend-callback-client.md"
depends_on: []
blocks: ["1.01", "1.05", "1.06"]
estimate: "M"
status: "done"
completed_at: "2026-05-25"
owner: ""
sprint: ""
---

# Task 1.02 — 메인 백엔드 콜백/컨텍스트 클라이언트 (워커 측)

> Spec: [`specs/phase-1/02-main-backend-callback-client.md`](../../specs/phase-1/02-main-backend-callback-client.md)

## 의존성

- 독립 task — 기존 `common/http_client/`·`common/clients/base_client.py`·`common/main_server/` 위에 구축한다. phase-1 내 선행 task 없음 (leaf).

## 사전 준비

- [x] `MAIN_BACKEND_URL` / `MAIN_BACKEND_API_KEY` 환경변수 정의 (.env.example 반영)
- [x] `common/http_client`(`request_with_retry`·`_parse_envelope`·`MainServerError`) 재사용 가능 여부 확인

## 구현 체크리스트

- [x] `slide-plan` 제출 콜백 (`POST /{job_id}/slide-plan`)
- [x] slide 레벨 이벤트 콜백 (`slide_content_ready`/`slide_preview_ready`/`slide_*_error`/`slide_regenerated`)
- [x] job 레벨 이벤트 콜백 (`pipeline_stage_changed`/`all_completed`)
- [x] 컨텍스트 조회: `GET /{job_id}`(portfolioText·slidePlan), `GET /{job_id}/slides/{slide_id}`(currentFills·sourceSlideId)
- [x] camelCase↔snake_case 매핑 (단 `slidePlan`/`currentFills` JSONB 내부 키는 snake 유지 — §11.0.3)
- [x] 재시도: 5xx/timeout 선형 백오프+jitter 최대 5회, 4xx 즉시 실패 전파

## Definition of Done

- [x] 각 콜백이 올바른 경로·바디(camelCase 최상위, JSONB 내부 snake)로 POST 됨을 mock 검증
- [x] 컨텍스트 응답이 워커 내부 snake_case 구조로 매핑됨을 검증
- [x] 5xx 5회 재시도 후 `MainServerError`, 4xx 즉시 실패 검증

## 리스크 / 메모

- `slidePlan.selected_slides[].source_slide_id`/`content_brief`, `currentFills[id].font_size_override`/`is_title` 등 blob 내부 snake 키를 camel 로 잘못 변환하지 않도록 매핑 예외(§11.0.3) 주의.
