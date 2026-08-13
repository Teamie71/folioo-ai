# 경험정리 로컬 개발 환경

> 설계: [에이전트 통합 문서](experience-map-agent.md) · [API 명세](experience-map-api-spec.md)
> 메인 서버 요청: [백엔드에 요청할 것](experience-map-backend-requests.md)

메인 서버 없이 AI 서버만 로컬에서 띄우고 테스트하기 위한 세팅입니다.
**sudo 도 Docker 도 필요 없습니다.**

---

## 1. 처음 한 번

```bash
uv sync --dev --group local-db                                  # 의존성 + PostgreSQL 바이너리
uv run --group local-db python scripts/experience_map/local_db.py init
cp .env.example .env                                            # 그리고 아래를 채웁니다
```

`init` 이 하는 일 — 클러스터 생성, 기동, database 2개 생성, 스키마 적용. **여러 번
실행해도 안전합니다.**

끝나면 `.env` 에 넣을 두 줄을 출력합니다.

```bash
DATABASE_URL=postgresql://postgres@127.0.0.1:55432/folioo_expmap
CHECKPOINT_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/folioo_checkpoint
```

**두 DB 를 반드시 분리합니다.** 같은 DB 를 쓰면 LangGraph 가 경험 맵 DB 에
checkpoint 테이블을 만듭니다. `CHECKPOINT_DATABASE_URL` 이 없으면 앱이 기동하지
않습니다 (태스크 3.01).

### 티켓 서명 키

프론트가 AI 서버에 직결하므로 티켓 검증이 필요합니다 (API 명세 2-1).
**`AI_SERVICE_API_KEY` 를 재사용하지 마세요.** HS256 이라 32바이트 이상이어야 합니다.

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

출력을 `.env` 의 `EXPMAP_TICKET_SECRET` 에 넣습니다.

### `.env` 최소 구성

```bash
DATABASE_URL=postgresql://postgres@127.0.0.1:55432/folioo_expmap
CHECKPOINT_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/folioo_checkpoint
AI_SERVICE_API_KEY=local-dev-api-key
EXPMAP_TICKET_SECRET=<위에서 만든 값>
EXPERIENCE_MAP_ENABLED=false
OPENROUTER_API_KEY=<LLM 노드를 돌릴 때만 필요>
```

---

## 2. 매일

```bash
# DB (재부팅하면 다시 start)
uv run --group local-db python scripts/experience_map/local_db.py start
uv run --group local-db python scripts/experience_map/local_db.py status
uv run --group local-db python scripts/experience_map/local_db.py stop

# 앱
uv run uvicorn app.main:app --reload

# 확인
curl -s localhost:8000/health | python -m json.tool
```

`/health` 가 이렇게 나오면 정상입니다.

```json
{
  "status": "ok",
  "checkpointer": "connected",
  "experience_map_db": "connected",
  "main_server": "disconnected",
  "api_key": "configured"
}
```

`main_server: disconnected` 는 정상입니다 — 로컬에 메인 서버가 없습니다.

### DB 들여다보기

```bash
uv run --group local-db python scripts/experience_map/local_db.py psql expmap
uv run --group local-db python scripts/experience_map/local_db.py psql checkpoint
```

### 처음부터 다시

```bash
uv run --group local-db python scripts/experience_map/local_db.py reset -y
```

### 테스트 돌리기

```bash
uv run pytest              # 전체
uv run pytest -rs          # 건너뛴 테스트를 이유와 함께 보여준다
```

**DB 가 떠 있어야 전부 돕니다.** 경험정리 테스트 60여 개(세션당 running 1건·lease
만료·재시도 원자성)는 실제 PostgreSQL 에 붙습니다. asyncpg 를 mock 으로 감싸면
동시성은 아무것도 검증하지 못하기 때문입니다.

DB 가 없으면 그것들이 **skip** 되고 나머지는 통과합니다. 로컬에서 DB 없이 빠르게
돌릴 수 있게 한 것인데, **skip 은 초록불로 보인다는 함정**이 있습니다. 동시성만
빠진 채 "통과" 로 끝납니다.

그래서 DB 가 반드시 돌아야 하는 자리에서는 이렇게 켭니다.

```bash
EXPMAP_REQUIRE_DB=1 uv run pytest    # DB 를 못 쓰면 skip 이 아니라 실패
```

CI 는 항상 이 값을 켜고 돌립니다 (아래 참고).

### CI

`.github/workflows/ci.yml` 이 모든 PR 에서 두 job 을 돌립니다.

| job | 하는 일 |
| --- | --- |
| `lint` | `ruff check` + `ruff format --check` |
| `test` | PostgreSQL 16 컨테이너를 띄우고 `EXPMAP_REQUIRE_DB=1` 로 전체 테스트 |

스키마는 로컬과 같은 `scripts/experience_map/schema.sql` 을 씁니다. 컨테이너에는
`.env` 가 없으므로 접속 주소는 `EXPMAP_TEST_DATABASE_URL` 로 넘깁니다.

CI 는 외부 API 를 치지 않습니다. `OPENROUTER_API_KEY` 에는 더미 값이 들어가고,
LLM 을 쓰는 테스트는 전부 mock 입니다.

---

## 3. API 수동 호출

메인 서버가 없으므로 티켓을 직접 만들어 씁니다.

```bash
uv run python scripts/experience_map/make_ticket.py --user-id 1
uv run python scripts/experience_map/make_ticket.py --user-id 1 --curl   # curl 명령 그대로 출력
```

거부 경로도 만들 수 있습니다.

```bash
--expires-in -1        # 만료된 티켓        → 401 ticket_expired
--secret wrong-secret  # 위조 서명          → 401 ticket_invalid
--session-id <다른값>  # 다른 세션의 티켓   → 403 session_forbidden
```

---

## 4. 지금 어디까지 되나

| 단계 | 볼 수 있는 것 | 필요한 태스크 | 상태 |
| --- | --- | --- | --- |
| 1 | 단위 테스트 (`uv run pytest`) | — | ✅ |
| 2 | API·SSE 가 붙고 이벤트가 흐름 | 3.04 + 3.10 | 대기 |
| 3 | 채팅 → LLM → 블록 구조화 | + 3.05, 3.11~3.17 | `block` 스키마 필요 |
| 4 | 커밋까지 end-to-end | + 3.18, 3.20 | 커밋 API mock 필요 |

**2단계까지는 메인 서버가 필요 없습니다.** AI 소유 테이블 DDL 이 명세 3-1~3-4 에
전부 있어서 로컬 스키마를 직접 만들 수 있습니다 (`schema.sql`).

---

## 5. 아직 막혀 있는 것

### `block` / `block_kind` DDL

명세에 **조회 쿼리(4-1)와 제약(3-5)만 있고 테이블 정의가 없습니다.** 추측해서
만들면 실제 스키마와 어긋나 나중에 다시 짜야 하므로 `schema.sql` 에 넣지
않았습니다.

**3.05(경험 맵 Repository) 착수 전에 메인 서버에서 두 테이블 DDL 을 받아야 합니다.**
migration 전체를 기다릴 필요는 없고 스키마만 있으면 3단계까지 열립니다.

### 커밋 API

`POST /commit` 은 메인 서버 몫입니다. 로컬 end-to-end 에는 mock 서버가 필요하며
3.18 착수 시점에 만듭니다.

---

## 6. 이 세팅에 대해

`scripts/experience_map/` 아래 셋입니다.

| 파일 | 역할 |
| --- | --- |
| `local_db.py` | 클러스터 관리 (`init`·`start`·`stop`·`status`·`psql`·`reset`) |
| `schema.sql` | 명세 3-1~3-4 의 DDL. **로컬 전용** |
| `make_ticket.py` | 티켓 발급 (메인 서버 대역) |

PostgreSQL 은 `pgserver` 패키지가 번들한 바이너리(16.2, 약 35MB)를 씁니다.
`local-db` 의존성 그룹은 **선택**이라 `uv sync --dev` 만으로는 받지 않습니다.

클러스터는 `.local/pgdata/` 에 만들어지며 `.gitignore` 에 있습니다.
포트는 기본 **55432** 이고 `EXPMAP_LOCAL_PG_PORT` 로 바꿀 수 있습니다.
시스템 PostgreSQL(5432)과 겹치지 않게 비켜 둔 값입니다.

> ⚠️ **`schema.sql` 은 운영 스키마가 아닙니다.** 운영은 메인 서버 migration 이
> 소유합니다(외부-A). migration 이 나오면 대조해서 차이를 없애야 합니다.
