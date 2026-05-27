# 시각화 워커 서비스 스캐폴드 및 Cloud Tasks Push 핸들러

## Purpose
`apps/pptx-worker/` 에 PPTX 시각화 워커(Cloud Run Service)의 FastAPI 진입점과 Cloud Tasks HTTP Push 핸들러 2종을 구축해, generate/regenerate 작업을 멱등하게 동기 처리할 수 있는 골격을 만든다.

## Requirements
- `POST /tasks/visualizations/generate` 와 `POST /tasks/visualizations/regenerate` 핸들러를 노출하고, Cloud Tasks payload(`messageType`/`jobId`/`idempotencyKey`/`callbackBaseUrl`/`schemaVersion` 등)를 파싱한다.
- 패턴 A(요청 안에서 Step 전체 동기 처리 후 200 OK)를 따르고, 재시도 분류로 503(retryable)·422/200(fatal)·200(skip)을 반환한다.
- §7.4.5 멱등 체크: 처리 전 메인 internal API 로 slide/job 상태를 조회해 `regenerating`/`generating` 이 아니면 200 으로 ACK 후 skip 한다.
- OIDC 토큰 검증은 Cloud Run IAM(`roles/run.invoker`)에 위임하고 워커 코드에는 인앱 토큰 검증을 두지 않는다.
- `GET /health` 와 누적 변환 N회(기본 20) 도달 시 인스턴스 자체 종료를 위한 lifetime 카운터를 제공한다.

## Approach
`apps/pptx-worker/app/main.py` 를 FastAPI 진입점으로 두고 `app/api/tasks.py` 에 두 push 핸들러를 둔다. 핸들러는 얇게 유지하고 실제 파이프라인은 `features/visualization/service.py` 오케스트레이션으로 위임한다(spec 05·07). 동시성은 `concurrency=1` 전제이며, soffice 변환 카운터가 임계치에 닿으면 200 응답 직후 프로세스를 종료해 Cloud Run 이 새 인스턴스를 띄우게 한다(메모리 누수 리셋, worker-spec.md §8.3). 빌드/배포는 인터뷰 챗 서비스와 분리하되 `common/` 패키지는 import 로 재사용한다(ADR-0001).

## Verification
- generate/regenerate push 를 보내면 payload 가 파싱되고 오케스트레이션이 호출되며 정상 시 200 을 반환한다.
- 같은 메시지를 두 번 push 했을 때(이미 terminal 상태) 워커가 작업을 재실행하지 않고 200 으로 ACK 한다.
- RetryableError 발생 시 503, FatalError 발생 시 에러 콜백 후 200(또는 422)을 반환하는지 단위 테스트로 검증한다.
- `GET /health` 가 `concurrent_active`/`lifetime_processed`/`ready_for_recycle` 를 반환하고, 누적 변환 N회 후 인스턴스가 종료되는지 확인한다.
