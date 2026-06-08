# PPTX 시각화 Main Backend Handoff

이 문서는 `folioo-ai` 저장소의 PPTX Worker 구현이 실제 사용자 기능으로 연결되기 위해
Main Backend와 운영 환경에서 닫아야 하는 계약을 정리한다. 상세 설계의 기준 문서는
`docs/architecture/pptx-gen-plan-v6.md`이고, Cloud Run/GCP 절차는
`deploy/pptx-worker/README.md`를 따른다.

## Worker Repo 상태

- Worker 런타임: `apps/pptx-worker/pptx_worker/main.py`
- Cloud Tasks push handler: `POST /tasks/visualizations/generate`,
  `POST /tasks/visualizations/regenerate`
- Main callback/context client: `features.visualization.main_client.VisualizationMainClient`
- GCS 직접 R/W: `features.visualization.storage.gcs_client.GcsClient`
- 기본 PPTX toolchain: 이미지 내부 `/app/apps/pptx-worker/toolchain`
- 배포 설정: `deploy/pptx-worker/cloud-run-service.yaml`

Worker는 DB, 사용자 인증, SSE, signed URL, 재생성 quota, Cloud Tasks enqueue를 담당하지 않는다.
이 항목은 Main Backend가 단독 소유한다.

## Main Backend 필수 Public API

프론트는 반드시 Main Backend만 호출한다.

| API | Main 책임 |
| --- | --- |
| `POST /api/visualizations` | 사용자/포트폴리오 소유권 검증, `visualization_jobs` 생성, `viz.generate` enqueue, `202 { jobId }` 반환 |
| `GET /api/visualizations/{jobId}/stream` | snapshot 즉시 발신, 이후 worker callback을 SSE로 fan-out |
| `GET /api/visualizations/{jobId}/slides` | slide 목록, status, signed preview URL, remaining regeneration count 반환 |
| `POST /api/visualizations/{jobId}/slides/{slideId}/regenerate` | Job row lock, slide CAS, quota 차감, `viz.regenerate` enqueue |
| `POST /api/visualizations/{jobId}/slides/{slideId}/retry` | error slide를 quota 차감 없이 `generating`으로 전이 후 retry enqueue |
| `GET /api/visualizations/{jobId}/export/status` | `compute_can_export()` 결과 반환 |
| `POST /api/visualizations/{jobId}/export` | `compute_can_export()` 재검증 후 PPTX/PDF signed URL 발급 |

`can_export`는 저장 컬럼이 아니라 파생 상태다. `job.status == completed`이고 모든 slide가
`completed`이며 `gcs_pptx_key`가 있을 때만 export 가능하다.

## Main Backend 필수 Internal API

Worker는 `MAIN_BACKEND_URL`과 `MAIN_BACKEND_API_KEY` 기반 `X-API-Key`로 아래 API를 호출한다.
응답 envelope은 `{ isSuccess, result }` 형식을 지원해야 한다.

| API | Main 책임 |
| --- | --- |
| `GET /internal/visualizations/{jobId}` | `portfolioText`, `templateId`, `slidePlan`, status 등 job context 반환 |
| `GET /internal/visualizations/{jobId}/slides/{slideId}` | `currentFills`, `sourceSlideId`, `slideFilename`, status 등 slide context 반환 |
| `POST /internal/visualizations/{jobId}/slide-plan` | slide rows 일괄 생성/조회, `slides[]`에 `id`를 포함해 반환 |
| `POST /internal/visualizations/{jobId}/slides/{slideId}/events` | slide 상태/preview/fills 갱신, signed preview URL 발급, SSE emit |
| `POST /internal/visualizations/{jobId}/events` | pipeline stage 또는 all_completed 처리, export 상태 재계산, SSE emit |

최상위 API 필드는 camelCase, `slidePlan`/`currentFills` JSONB 내부 필드는 snake_case를 유지한다.
Worker는 callback마다 이벤트 단위 idempotency key를 보내므로 Main은 해당 key로 중복 처리를 막아야 한다.

## Cloud Tasks 계약

Main Backend는 큐에만 enqueue하고 Worker를 직접 호출하지 않는다.

Generate payload:

```json
{
  "messageType": "viz.generate",
  "jobId": "job-id",
  "portfolioId": "portfolio-id",
  "userId": "user-id",
  "templateId": "template-id",
  "callbackBaseUrl": "https://main-backend.example",
  "idempotencyKey": "uuid",
  "schemaVersion": 1
}
```

Regenerate/retry payload:

```json
{
  "messageType": "viz.regenerate",
  "jobId": "job-id",
  "slideId": "slide-id",
  "userRequest": "제목을 조금 키워줘",
  "isRetry": false,
  "callbackBaseUrl": "https://main-backend.example",
  "idempotencyKey": "uuid",
  "schemaVersion": 1
}
```

`isRetry=true`이면 `userRequest`는 생략한다. Cloud Tasks HTTP target은
`oidc_token.service_account_email = WORKER_OIDC_SERVICE_ACCOUNT`,
`audience = WORKER_URL`로 설정한다.

## 상태 전이와 CAS

- Generate 시작: `visualization_jobs.status=pending` 생성 후 enqueue.
- Worker generate 처리 가능 상태: `pending`, `generating`.
- Slide plan callback: slide rows를 `pending`으로 생성하고 `slide_plan_ready` SSE.
- `slide_content_ready`: slide `generating`.
- `slide_preview_ready`: slide `completed`, signed preview URL SSE.
- `slide_preview_error`: Phase 1이면 slide `error`, Phase 2 재생성이면 이전 completed로 rollback.
- `all_completed`: 성공 slide가 있으면 job `completed` 또는 `partial_error`, 전체 실패면 `error`.
- Regenerate 시작: `completed -> regenerating` CAS와 quota 차감을 같은 transaction에서 처리.
- Retry 시작: `error -> generating` CAS, quota 미차감.
- `slide_regenerated`: slide `completed`, current fills/preview 갱신, 남은 error가 없으면 job 재평가.

Stuck recovery cron은 `pending/generating/regenerating`이 일정 시간 이상 멈춘 경우 error 또는 rollback으로
정리하고, 사용자 재생성 quota 보상까지 담당한다.

## GCS와 Signed URL

Worker가 직접 쓰는 canonical key:

- `jobs/{jobId}/current.pptx`
- `jobs/{jobId}/current.pdf`
- `jobs/{jobId}/previews/slide-{slideOrder:02d}.jpg`

Worker가 재생성 중 임시로 쓰는 staging key:

- `jobs/{jobId}/attempts/{attemptId}/current.pptx`
- `jobs/{jobId}/attempts/{attemptId}/current.pdf`
- `jobs/{jobId}/attempts/{attemptId}/previews/slide-{slideOrder:02d}.jpg`
- `jobs/{jobId}/attempts/{attemptId}/rollback/*`

Main은 signed URL 발급 전용이다. Worker의 attempt key는 프론트 signed URL 대상이 아니며,
Main은 callback payload의 canonical key만 사용자에게 노출한다.

재생성 경로에서 Worker는 attempt 업로드 후 `slide_regenerated` callback이 2xx로 성공해야
canonical promote를 수행한다. 따라서 Main은 `slide_regenerated` 처리와 SSE emit을 idempotent하게
구성하고, promote 실패 재시도 중에는 이전 preview 유지 또는 pending 표시 정책을 정해야 한다.
부분 promote/rollback 실패를 감지할 운영 알림과 수동 복구 절차도 필요하다.

## 배포 전 확인

- `uv run ruff check .`
- `uv run pytest tests/test_features/test_visualization tests/test_pptx_worker -q`
- Docker image build 및 `apps/pptx-worker/scripts/verify_runtime_image.sh`
- `deploy/pptx-worker/README.md`의 Cloud Run/IAM/GCS/Secret verification
- 최소 1개 템플릿의 `template.pptx`, `meta.json`, `thumbnail.jpg` GCS 업로드
- Main Backend에서 실제 Cloud Tasks push smoke: generate 1건, regenerate 1건, export 1건

## 운영 설정 요약

- Secret Manager: `MAIN_BACKEND_API_KEY`, `OPENROUTER_API_KEY`
- Worker env: `MAIN_BACKEND_URL`, `MAIN_BACKEND_TIMEOUT`, `OPENROUTER_BASE_URL`,
  `LLM_MODEL_NAME`, `FILE_PROCESSOR_MODEL_NAME`, `ANTHROPIC_PPTX_SKILL_DIR`
- Worker SA: Secret Accessor, bucket-level Storage Object Admin
- Cloud Tasks SA: Cloud Run Invoker
- Main Backend runtime principal: Cloud Tasks Enqueuer, Cloud Tasks SA actAs
- GCS lifecycle: `jobs/*/attempts/**`와 `rollback/**` 보존 기간 설정
