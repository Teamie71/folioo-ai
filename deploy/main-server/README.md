# Main Server Cloud Run Deployment

이 디렉터리는 루트 FastAPI/LangGraph 인터뷰 서버(`app/main.py`)를 `pptx-worker`와
분리된 Cloud Run 서비스로 배포하기 위한 이미지 빌드, 서비스 사양을 담는다.

DB(Supabase Postgres)는 그대로 유지하고 `DATABASE_URL` / `CHECKPOINT_DATABASE_URL`로
외부 접속만 한다. VPC 커넥터는 필요 없다 (Supabase가 공인 접속을 지원).

## Variables

```bash
export PROJECT_ID="folioo-prod"
export REGION="asia-northeast3"
export SERVICE="folioo-main-server"
export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/folioo-ai/main-server:latest"
export MAIN_SERVER_RUNTIME_SERVICE_ACCOUNT="folioo-main-server@${PROJECT_ID}.iam.gserviceaccount.com"
```

## Secrets

다음 시크릿을 Secret Manager에 먼저 등록한다 (`.env` 실값 기준):

```bash
for name in database-url checkpoint-database-url openrouter-api-key \
    langsmith-api-key tavily-api-key ai-service-api-key \
    main-backend-api-key expmap-ticket-secret; do
  gcloud secrets create "$name" --project "$PROJECT_ID" --replication-policy=automatic || true
done

echo -n "$DATABASE_URL" | gcloud secrets versions add database-url --project "$PROJECT_ID" --data-file=-
# ... 나머지 시크릿도 동일하게 versions add
```

`cloud-run-service.yaml`의 `${..._SECRET}` 치환값은 위에서 만든 시크릿 이름(예:
`database-url`)을 넣는다.

## Build

```bash
gcloud builds submit \
  --project "$PROJECT_ID" \
  --config deploy/main-server/cloudbuild.yaml \
  --substitutions "_IMAGE_URI=${IMAGE_URI}" \
  .
```

## Deploy

```bash
export LLM_MODEL_NAME="..."
export PDF_EXTRACTION_MODEL_NAME="..."
export FILE_PROCESSOR_MODEL_NAME="..."
export MAIN_BACKEND_URL="https://..."
export ALLOWED_ORIGINS="https://..."
export INSIGHT_SEARCH_TOP_K="5"
export INSIGHT_SEARCH_THRESHOLD="0.7"
export DATABASE_URL_SECRET="database-url"
export CHECKPOINT_DATABASE_URL_SECRET="checkpoint-database-url"
export OPENROUTER_API_KEY_SECRET="openrouter-api-key"
export LANGSMITH_API_KEY_SECRET="langsmith-api-key"
export TAVILY_API_KEY_SECRET="tavily-api-key"
export AI_SERVICE_API_KEY_SECRET="ai-service-api-key"
export MAIN_BACKEND_API_KEY_SECRET="main-backend-api-key"
export EXPMAP_TICKET_SECRET_SECRET="expmap-ticket-secret"

envsubst < deploy/main-server/cloud-run-service.yaml > /tmp/folioo-main-server.yaml

gcloud run services replace /tmp/folioo-main-server.yaml \
  --project "$PROJECT_ID" \
  --region "$REGION"
```

이 서비스는 프론트/메인 백엔드에서 직접 호출되는 공개 API이므로 Cloud Run IAM
invoker 체크를 끄고(`invoker-iam-disabled: "true"`), 인증은 앱 레벨 `X-API-Key` /
티켓(Bearer)으로 처리한다 (`app/middleware/auth.py`).

## Verify

```bash
SERVICE_URL="$(gcloud run services describe "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format 'value(status.url)')"

curl -sS "$SERVICE_URL/health"
```

`checkpointer`, `experience_map_db`가 `connected`인지 확인한다. 확인 후 기존
PaaS(Render/Railway/Fly 등) 인스턴스는 트래픽 전환이 끝날 때까지 유지하다가
내린다.
