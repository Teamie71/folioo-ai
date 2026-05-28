---
id: "1.01"
phase: 1
title: "시각화 워커 서비스 스캐폴드 및 Cloud Tasks Push 핸들러"
spec: "specs/phase-1/01-worker-service-scaffold.md"
depends_on: ["1.02"]
blocks: ["1.09", "1.10", "1.20", "1.22"]
estimate: "M"
status: "done"
completed_at: "2026-05-27"
owner: ""
sprint: ""
---

# Task 1.01 — 시각화 워커 서비스 스캐폴드 및 Cloud Tasks Push 핸들러

> Spec: [`specs/phase-1/01-worker-service-scaffold.md`](../../specs/phase-1/01-worker-service-scaffold.md)

## 의존성

- 1.02 (메인 백엔드 콜백/컨텍스트 클라이언트) — §7.4.5 멱등 체크가 처리 전 메인 internal API 로 slide/job 상태를 조회하므로 콜백 클라이언트가 선행돼야 한다. (오케스트레이션 05·07 은 핸들러가 위임만 하므로 역의존 — 본 task 의 선행 아님)

## 사전 준비

- [x] `apps/pptx-worker/` 빌드 타깃 분리 (인터뷰 챗과 별도 배포, `common/` 직접 import — ADR-0001)
- [x] Cloud Tasks payload 스키마(`messageType`/`jobId`/`idempotencyKey`/`callbackBaseUrl`/`schemaVersion`) 확인

## 구현 체크리스트

- [x] `app/main.py` FastAPI 진입점 + `app/api/tasks.py` push 핸들러 2종
- [x] `POST /tasks/visualizations/generate` payload 파싱 → `features/visualization/service.py` 위임 (05)
- [x] `POST /tasks/visualizations/regenerate` payload 파싱 → 위임 (07)
- [x] 패턴 A: 요청 안에서 동기 처리 후 200, 재시도 분류 503(retryable)/422·200(fatal)/200(skip)
- [x] §7.4.5 멱등 체크: 메인 API 로 상태 조회 후 `regenerating`/`generating` 아니면 200 ACK skip
- [x] OIDC 검증은 Cloud Run IAM 위임 (인앱 토큰 검증 없음)
- [x] `GET /health` + 누적 변환 N회(기본 20) 도달 시 인스턴스 자체 종료 lifetime 카운터

## Definition of Done

- [x] generate/regenerate push 시 payload 파싱·오케스트레이션 호출·정상 200 검증
- [x] terminal 상태 메시지 재 push 시 재실행 없이 200 ACK
- [x] RetryableError→503, FatalError→에러 콜백 후 200(또는 422) 단위 테스트
- [x] `/health` 가 concurrent_active/lifetime_processed/ready_for_recycle 반환, N회 후 종료

## 리스크 / 메모

- 핸들러는 얇게: 실제 파이프라인은 05·07 로 위임. 위임 대상 인터페이스를 먼저 stub 으로 두고 05·07 완료 시 연결.
- lifetime 카운터는 1.09 메트릭(`worker_jobs_processed_total`)과 동일 소스 공유.
