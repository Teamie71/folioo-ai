---
id: "1.09"
phase: 1
title: "시각화 워커 관측성·모니터링 메트릭"
spec: "specs/phase-1/09-worker-observability-metrics.md"
depends_on: ["1.01", "1.04", "1.05"]
blocks: []
estimate: "S"
status: "todo"
owner: ""
sprint: ""
---

# Task 1.09 — 시각화 워커 관측성·모니터링 메트릭

> Spec: [`specs/phase-1/09-worker-observability-metrics.md`](../../specs/phase-1/09-worker-observability-metrics.md)

## 의존성

- 1.04 (soffice 렌더) — soffice 변환 duration·RSS·실패 사유·tmp 디스크를 soffice 래퍼 수명주기에서 계측 (PPTX 도구 체인 1.11·GCS 1.12 는 불요)
- 1.01 (서비스 스캐폴드) — `/health`·lifetime 카운터와 `worker_jobs_processed_total` 동일 소스 공유
- 1.05 (Phase 1 파이프라인) — 오케스트레이션 계측 훅에서 emit

## 사전 준비

- [ ] 메트릭 노출 방식 결정(Prometheus exporter 등) + 모니터링 백엔드 연동 지점 확인

## 구현 체크리스트

- [ ] `soffice_rss_bytes`, `soffice_conversion_duration_seconds{quantile}`(P50/P95/P99)
- [ ] `soffice_conversion_failures_total{reason=timeout|oom|기타}`, `worker_oom_kill_total`
- [ ] `worker_jobs_processed_total`(1.01 lifetime 카운터와 동일 소스), `tmp_disk_bytes_used`, `font_fallback_warnings_total`
- [ ] 알람 기준: P95 duration>30s 5분 지속→경고, oom_kill 증가→즉시, font_fallback 증가→폰트 누락 알림
- [ ] emit 지점: soffice 래퍼(04)·오케스트레이션(05)·`/health`(01) 계측 훅

## Definition of Done

- [ ] 정상 변환 시 duration 분위수·RSS 갱신, 타임아웃/OOM 실패가 reason 별 카운트 검증
- [ ] `worker_jobs_processed_total` 이 1.01 lifetime 카운터와 일치, N회 도달 시 ready_for_recycle 연동 검증
- [ ] 폰트 누락 변환 시 `font_fallback_warnings_total` 증가, P95>30s 경고 임계 충족 검증

## 리스크 / 메모

- 워커는 메트릭 노출까지만 책임. 알람 임계는 운영 대시보드/모니터링 백엔드에서 구성.
