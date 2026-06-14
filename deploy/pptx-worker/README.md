# PPTX Worker Cloud Run Deployment

이 디렉터리는 `apps/pptx-worker`를 인터뷰 챗 서비스와 분리된 Cloud Run 서비스로
배포하기 위한 이미지 빌드, 서비스 사양, IAM 확인 절차를 담는다.

## Variables

```bash
export PROJECT_ID="folioo-prod"
export REGION="asia-northeast3"
export SERVICE="folioo-pptx-worker"
export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/folioo-ai/pptx-worker:latest"
export WORKER_RUNTIME_SERVICE_ACCOUNT="folioo-pptx-worker@${PROJECT_ID}.iam.gserviceaccount.com"
export CLOUD_TASKS_SERVICE_ACCOUNT="folioo-cloud-tasks@${PROJECT_ID}.iam.gserviceaccount.com"
export TASK_ENQUEUER_PRINCIPAL="serviceAccount:main-backend@${PROJECT_ID}.iam.gserviceaccount.com"
```

## Build

```bash
gcloud builds submit \
  --project "$PROJECT_ID" \
  --config deploy/pptx-worker/cloudbuild.yaml \
  --substitutions "_IMAGE_URI=${IMAGE_URI}" \
  .
```

로컬에서 이미지를 확인할 때는 다음 스모크 체크를 실행한다.

```bash
docker run --rm \
  --entrypoint bash \
  "$IMAGE_URI" \
  apps/pptx-worker/scripts/verify_runtime_image.sh
```

이미지 build smoke는 번들된 `ANTHROPIC_PPTX_SKILL_DIR` 을 기준으로
`/app/apps/pptx-worker/toolchain` 경로를 검증하고, 환경변수 미설정 시 빠르게 실패하는
경로도 함께 확인한다. 동일한 스크립트를 runtime 환경에서 실행해
`PptxToolchain.ensure_available()`까지 확인한다.

## Deploy

Cloud Run 서비스는 `concurrency=1`, `timeout=1800s`, max instances 20, 2 vCPU,
컨테이너 메모리 limit 5120Mi, `/tmp` 1Gi 메모리 볼륨, `JAVA_TOOL_OPTIONS=-Xmx512m`
으로 고정한다.

```bash
envsubst < deploy/pptx-worker/cloud-run-service.yaml > /tmp/folioo-pptx-worker.yaml

gcloud run services replace /tmp/folioo-pptx-worker.yaml \
  --project "$PROJECT_ID" \
  --region "$REGION"

gcloud run services update "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --invoker-iam-check
```

## IAM

Cloud Run Invoker IAM check는 켜둔다. 워커 앱 코드에서는 OIDC 토큰을 직접 검증하지
않고 Cloud Run IAM에 위임한다.

```bash
gcloud run services add-iam-policy-binding "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --member "serviceAccount:${CLOUD_TASKS_SERVICE_ACCOUNT}" \
  --role "roles/run.invoker"

gcloud run services remove-iam-policy-binding "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --member "allUsers" \
  --role "roles/run.invoker" \
  --quiet || true

gcloud iam service-accounts add-iam-policy-binding "$CLOUD_TASKS_SERVICE_ACCOUNT" \
  --project "$PROJECT_ID" \
  --member "$TASK_ENQUEUER_PRINCIPAL" \
  --role "roles/iam.serviceAccountUser"
```

Cloud Tasks enqueue 쪽 HTTP target은 `oidc_token.service_account_email`에
`$CLOUD_TASKS_SERVICE_ACCOUNT`를 넣고, audience는 Cloud Run service URL로 맞춘다.
Task 를 생성하는 메인 백엔드 런타임 주체는 `$CLOUD_TASKS_SERVICE_ACCOUNT`에 대한
`iam.serviceAccounts.actAs` 권한(`roles/iam.serviceAccountUser`)이 필요하다.

## Verify

서비스 사양:

```bash
gcloud run services describe "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format export > /tmp/folioo-pptx-worker.actual.yaml
```

확인할 값은 다음과 같다.

- `metadata.annotations["run.googleapis.com/invoker-iam-disabled"] == "false"`
- `spec.template.metadata.annotations["autoscaling.knative.dev/maxScale"] == "20"`
- `spec.template.spec.containerConcurrency == 1`
- `spec.template.spec.timeoutSeconds == 1800`
- `spec.template.spec.containers[0].resources.limits.cpu == "2"`
- `spec.template.spec.containers[0].resources.limits.memory == "5120Mi"`
- `/tmp` volume `emptyDir.sizeLimit == "1Gi"`
- `JAVA_TOOL_OPTIONS == "-Xmx512m"`

IAM 경로:

```bash
WORKER_URL="$(gcloud run services describe "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format 'value(status.url)')"

curl -sS -o /tmp/unauth.out -w "%{http_code}\n" "$WORKER_URL/health"

TOKEN="$(gcloud auth print-identity-token \
  --audiences "$WORKER_URL" \
  --impersonate-service-account "$CLOUD_TASKS_SERVICE_ACCOUNT")"

curl -sS -H "Authorization: Bearer ${TOKEN}" "$WORKER_URL/health"
```

첫 번째 호출은 401 또는 403이어야 하고, 두 번째 호출은 200이어야 한다.
