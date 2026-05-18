# 작업 큐는 GCP Cloud Tasks (HTTP Push) 를 사용한다

Status: accepted (supersedes the SQS assumption in `.for_local/main-backend-handoff.md` v1)

PPTX 시각화 워커는 Cloud Run 서비스로 배포되며 (ADR-0001), "HTTP 요청 = 작업 1개" 모델이 워크로드 추상화에 가장 단순하다. **Cloud Tasks 의 HTTP Push 와 Cloud Run 의 request-driven 모델은 1:1 정합** 이며, soffice 메모리 프로파일에 맞춘 `concurrency=1` 정책은 Cloud Tasks 의 `maxConcurrentDispatches` 로 그대로 외부화된다. SQS Long Polling 으로 가면 Cloud Run 안에 별도 puller 컨테이너 또는 사이드카가 필요해 운영 포인트가 늘어난다.

결정: Frontend → 메인 백엔드 (NestJS) 에서 받은 작업은 메인이 **Cloud Tasks 단일 큐 (`viz-jobs`) 로 enqueue** 하고, **Cloud Tasks 가 OIDC 토큰을 붙여 시각화 워커의 `POST {WORKER_URL}/tasks/visualizations/{generate|regenerate|export-pdf}` 로 HTTP Push** 한다. 재시도 / DLQ / 백오프 정책은 Cloud Tasks 큐 설정에 위임한다.

## Consequences

- `.for_local/main-backend-handoff.md` 의 SQS 가정은 폐기 — 본 ADR 이 적용된 시점에 같은 문서를 Cloud Tasks 기준으로 재작성한다.
- 메인 백엔드의 큐 클라이언트는 `@google-cloud/tasks` 사용 (`AWS SDK / @aws-sdk/client-sqs` 미사용).
- 워커는 SQS poller 컨테이너 / 사이드카 없이 **FastAPI 라우터 1개로 push 수신** → ADR-0001 의 모노레포 + 별도 배포 정책과도 깨끗이 맞물림.
- 멱등성은 큐가 보장해 주지 않으므로 (`MessageDeduplicationId` 미지원) **`idempotencyKey` 를 페이로드에 실어 메인 + 워커가 각자 dedup** 한다 — v5 §7.4.5 참조.
- 환경 정합성 전제: 인프라가 GCP 라는 가정 위에서만 성립. 메인 백엔드 / Postgres / S3(또는 GCS) 가 모두 GCP 인지는 인프라 결정 단계에서 별도 확인.
