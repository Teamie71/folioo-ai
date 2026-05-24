# 시각화 워커 관측성 · 모니터링 메트릭

## Purpose
시각화 워커의 soffice 변환 비용·메모리 누수·폰트 누락을 조기에 감지하기 위해 운영 메트릭을 계측하고 알람 기준을 정의해, 인스턴스 재활용과 용량 결정을 데이터로 뒷받침한다.

## Requirements
- `soffice_rss_bytes`, `soffice_conversion_duration_seconds{quantile}`(P50/P95/P99), `soffice_conversion_failures_total{reason=timeout|oom|기타}`, `worker_oom_kill_total`, `worker_jobs_processed_total`, `tmp_disk_bytes_used`, `font_fallback_warnings_total` 메트릭을 노출한다.
- 알람 기준을 정의한다: P95 `conversion_duration` > 30s 가 5분 지속되면 경고, `worker_oom_kill_total` 증가 시 즉시 알람, `font_fallback_warnings_total` 증가 시 폰트 누락으로 보고 디자이너에게 알린다.
- `worker_jobs_processed_total` 은 인스턴스 재활용(누적 변환 N회 후 자체 종료) 시점 결정에 쓰이도록 spec 01 의 lifetime 카운터와 동일 소스를 공유한다.
- 메트릭 emit 지점은 soffice 래퍼(spec 04)와 파이프라인 오케스트레이션(spec 05)의 계측 훅에 둔다.

## Approach
spec 01 의 `GET /health`(`concurrent_active`/`lifetime_processed`/`ready_for_recycle`)·인스턴스 재활용 카운터와 연동해, 같은 카운터를 메트릭으로도 노출한다(worker-spec.md §8.3.7). soffice 변환 duration·RSS·실패 사유는 soffice 래퍼(spec 04)의 서브프로세스 수명주기에서, 폰트 fallback 경고는 변환 로그 파싱에서 emit 한다. `tmp_disk_bytes_used` 는 작업 디렉터리 정리 누수를 잡는 무상태 검증 지표로 둔다. 알람은 운영 대시보드/모니터링 백엔드 쪽에서 임계치로 구성하고, 워커는 메트릭 노출까지만 책임진다.

## Verification
- 정상 변환 시 `soffice_conversion_duration_seconds` 분위수(P50/P95/P99)와 `soffice_rss_bytes` 가 갱신되고, 타임아웃/OOM 실패가 `soffice_conversion_failures_total{reason}` 에 사유별로 카운트되는지 검증한다.
- `worker_jobs_processed_total` 이 spec 01 의 lifetime 카운터와 일치하며, 누적 변환 N회 도달 시 `ready_for_recycle` 신호와 함께 인스턴스 종료를 유발하는지 확인한다.
- 의도적으로 폰트가 누락된 슬라이드를 변환하면 `font_fallback_warnings_total` 이 증가하고, P95 conversion_duration 이 30s 를 넘는 시나리오에서 경고 임계가 충족되는지 확인한다.
