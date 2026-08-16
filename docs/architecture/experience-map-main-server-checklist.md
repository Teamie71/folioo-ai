# 경험정리 메인 서버 연결 체크리스트

> 설계: [에이전트 통합 문서](experience-map-agent.md) · [API 명세](experience-map-api-spec.md)
> 상세 요청: [백엔드에 요청할 것](experience-map-backend-requests.md)

AI 서버와 메인 서버를 실제로 붙이기까지 필요한 것을 **순서대로** 모았습니다.
AI 서버 쪽 노드·API·인증은 구현이 끝났고, **남은 것은 대부분 메인 서버 쪽입니다.**

---

## 0. 지금 막혀 있는 것

| # | 필요한 것 | 급함 | 없으면 |
| --- | --- | --- | --- |
| 1 | `block` · `block_kind` 테이블 DDL | **높음** | 경험 맵을 못 읽어 end-to-end 자체가 불가 |
| 2 | `ai_experience_request.owner_token` 컬럼 | **높음** | 운영에서 `UndefinedColumnError`. 동시성 보호가 동작하지 않음 |
| 3 | `GET /templates` 응답 구조 확정 | 중간 | 가정한 형태와 다르면 파싱 수정 |
| 4 | AI → 메인 API 키 **값** 합의 | 중간 | 커밋·템플릿 호출이 전부 401 |

**1·2 는 메인 DB migration 이 나오기 전인 지금이 가장 쌉니다.** migration 이 나간
뒤에는 `ALTER TABLE` 을 따로 협의해야 합니다.

---

## 1. DB 스키마

### 1-1. AI 서버가 **읽는** 테이블 (메인 서버 소유)

- [ ] `experience_map` — `user_id`, `map_version`
- [ ] `block` — `id`, `parent_id`, `level`, `kind`, `position`, `content`, `placeholder`, `user_id`
- [ ] `block_kind` — `kind`, `placeholder`, `is_text_editable`, `is_deletable`
- [ ] AI 서버 DB 계정이 위 셋에 **`SELECT` 만** 가능한지 확인

AI 서버는 명세 4-1 의 쿼리를 그대로 씁니다.

```sql
SELECT b.id, b.parent_id, b.level, b.kind, b.position, b.content,
       COALESCE(b.placeholder, k.placeholder) AS placeholder,
       k.is_text_editable, k.is_deletable
  FROM block b JOIN block_kind k ON k.kind = b.kind
 WHERE b.user_id = $1
 ORDER BY b.level, b.parent_id, b.position, b.id;
```

> ⚠️ **아직 한 번도 실제 테이블에 실행해 본 적이 없습니다.** 로컬·CI 에는 이 두
> 테이블이 없어 테스트는 in-memory 대역으로 돌고 있습니다.

### 1-2. AI 서버가 **쓰는** 테이블

- [ ] `ai_experience_session` — 명세 3-2
- [ ] `ai_experience_request` — 명세 3-3
- [ ] **`ai_experience_request.owner_token uuid`** — 명세에 없는 추가 컬럼
- [ ] `ai_commit_log` — 명세 3-4

**`owner_token` 은 nullable 이어야 합니다.** 완료·실패·만료 정리 세 경로가 이 값을
`NULL` 로 되돌려 실행권을 회수합니다. `NOT NULL` 이면 그 세 경로가 전부 제약 위반이
됩니다.

- [ ] 세션당 running 1건 제약 (partial unique index) 확인

---

## 2. 메인 서버가 구현할 API

AI 서버가 호출합니다. `X-API-Key` 로 인증합니다 (아래 4절).

### 2-1. `POST /api/v1/experience-map/commit`

- [ ] 요청 payload

```json
{
  "user_id": "1",
  "request_id": "uuid",
  "base_map_version": 43,
  "items": [ /* StructuredItem */ ]
}
```

- [ ] 응답에 `request_id`, `previous_version`(또는 `revert_to_version`), `can_revert` 포함
- [ ] **`request_id` 기준 멱등** — 같은 값으로 재호출해도 중복 반영되지 않을 것
- [ ] `level`·`position` 은 **메인 서버가 계산** (AI 는 보내지 않음)

### 2-2. `GET /api/v1/experience-map/commit/{request_id}`

응답 유실 복구용입니다. lease 가 만료된 요청을 정리하기 전에 먼저 확인합니다.

- [ ] `{"committed": false}` 또는 `{"committed": true, "result": { ... }}`
- [ ] `result` 는 2-1 응답과 같은 형태

> 이게 없으면 **메인에는 커밋됐는데 AI 는 실패로 아는** 상태가 남습니다. 사용자가
> 같은 내용을 두 번 커밋하게 됩니다.

### 2-3. `GET /api/v1/experience-map/templates`

- [ ] 응답 구조 확정 — [백엔드 요청 문서](experience-map-backend-requests.md) 3번
- [ ] level 4 슬롯 10개(템플릿에 속하지 않음)와 level 5 슬롯 28개를 **모두 담을 수 있는 형태**

`slot_id` 형식은 level 에 따라 둘입니다. 점 개수가 곧 level 입니다.

```text
level 4 : {SECTION}.{SLOT}              DETAIL.MOTIVATION
level 5 : {SECTION}.{TEMPLATE}.{SLOT}   PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE
```

### 2-4. 오류 응답 코드

AI 서버가 코드로 분기합니다. **코드 문자열이 정확해야 합니다.**

| 코드 | 상황 | AI 서버 동작 |
| --- | --- | --- |
| `map_version_conflict` | `base_map_version` 이 최신이 아님 | 최신 맵 재조회 후 **1회** 재실행. 두 번째 충돌은 `commit_conflict` 로 종료 |
| `unknown_slot_id` | 모르는 `slot_id` | 템플릿 카탈로그 강제 갱신 후 **정확히 1회** 재시도 |
| `request_id_reused` | 같은 `request_id` 에 다른 내용 | 오류로 종료 |
| `map_not_initialized` | 경험 맵이 아직 없음 | 오류로 종료 |

- [ ] `map_version_conflict` 응답에 `current_map_version` 포함

---

## 3. AI 서버가 제공하는 API

메인 서버가 호출합니다. prefix 는 `/api/v1/experience-map`.

| 메서드 | 경로 | 호출자 | 인증 |
| --- | --- | --- | --- |
| `POST` | `/sessions` | **메인 서버** | `X-API-Key` |
| `GET` | `/sessions/{session_id}/state` | 프론트 | `Bearer {ticket}` |
| `GET` | `/sessions/{session_id}/requests/{request_id}` | 프론트 | `Bearer {ticket}` |
| `POST` | `/sessions/{session_id}/chat/stream` | 프론트 | `Bearer {ticket}` |
| `POST` | `/sessions/{session_id}/retry/stream` | 프론트 | `Bearer {ticket}` |

- [ ] 메인 서버가 `POST /sessions` 를 호출해 세션을 확보한다 — 요청 body 는 `{"user_id": "1"}` 뿐
- [ ] 메인 서버가 응답의 `session_id` 를 `sid` 로 넣어 티켓을 발급한다

**`session_id` 는 AI 서버가 발급합니다.** 요청 스키마에 `session_id` 필드가 없어
보내도 무시됩니다.

**사용자당 세션은 1개이고, 이 호출은 멱등입니다.** 이미 있으면 기존 세션을 그대로
돌려줍니다 (`ON CONFLICT (user_id)`). 티켓을 새로 발급할 때마다 호출해도 안전하며,
새 세션이 생기지 않습니다.

> 자주 하는 실수 — 티켓을 먼저 만들고 그 `sid` 로 접근하면 **그 세션이 DB 에 없어
> `404 session_not_found`** 가 납니다. 반드시 `POST /sessions` 로 받은 값을 씁니다.

### 티켓 계약

- [ ] HS256, 서명 키는 `EXPMAP_TICKET_SECRET` (양쪽 공유)
- [ ] claim: `sub`(user_id), `sid`(session_id), `exp`
- [ ] 기본 TTL 300초

AI 서버는 **서명 → 만료 → `sid` 일치** 순으로 검증합니다. `sid` 가 경로의
`session_id` 와 다르면 `403 session_forbidden` 입니다. 이 검사가 없으면 A 사용자의
유효한 티켓으로 B 사용자의 세션을 건드릴 수 있습니다.

---

## 4. 인증 키 — 방향마다 다릅니다

| 방향 | 헤더 | AI 서버 변수 |
| --- | --- | --- |
| 메인 → AI | `X-API-Key` | `AI_SERVICE_API_KEY` |
| AI → 메인 | `X-API-Key` | `MAIN_BACKEND_API_KEY` |

- [ ] **메인 서버는 키를 두 개 따로 보관**해야 합니다
- [ ] AI → 메인 호출을 검증할 값 = AI 쪽 `MAIN_BACKEND_API_KEY` 값
- [ ] `EXPMAP_TICKET_SECRET` 은 위 두 키와 **모두 달라야** 합니다

변수 이름은 각 서버 사정이고, 실제로 오가는 것은 헤더 값입니다. **합의할 것은 값
뿐입니다.** 아웃바운드 키는 이미 운영에서 쓰이고 있어 새로 발급할 필요가 없습니다.

한 키로 묶지 않는 이유는 티켓 서명 키를 분리한 것과 같습니다 — 회전 주기가 다르고,
한쪽이 유출돼도 반대 방향 호출 권한까지 넘어가지 않습니다.

---

## 5. 환경변수

### AI 서버

- [ ] `DATABASE_URL` — 경험 맵·세션·요청 DB
- [ ] `CHECKPOINT_DATABASE_URL` — **필수.** 없으면 서버가 뜨지 않습니다 (fallback 제거됨)
- [ ] `MAIN_BACKEND_URL`
- [ ] `AI_SERVICE_API_KEY` — 인바운드
- [ ] `MAIN_BACKEND_API_KEY` — 아웃바운드
- [ ] `EXPMAP_TICKET_SECRET` — 32바이트 이상
- [ ] `EXPMAP_UPLOAD_BUCKET` — GCS 버킷 + 권한
- [ ] `ALLOWED_ORIGINS` — 프론트 직결이라 웹 오리진 추가 필요
- [ ] `EXPERIENCE_MAP_ENABLED` — 검증 전까지 `false`

> ⚠️ **`CHECKPOINT_DATABASE_URL` 은 코드 배포보다 먼저 설정해야 합니다.** 순서가
> 바뀌면 앱이 기동하지 않습니다. 경험 맵 DB 와 **반드시 다른 database** 여야 하며,
> 같으면 LangGraph 가 메인 소유 DB 에 checkpoint 테이블을 만듭니다.

### 메인 서버

- [ ] `AI_SERVICE_URL`
- [ ] `AI_SERVICE_API_KEY` — AI 호출용
- [ ] AI 인바운드 검증용 키 (이름은 메인 서버가 정함)
- [ ] `EXPMAP_TICKET_SECRET` — AI 와 공유
- [ ] `EXPMAP_TICKET_TTL_SECONDS` — 기본 300

---

## 6. 배포 순서

선후 관계가 있는 항목입니다. 순서가 바뀌면 기동 실패하거나 런타임 오류가 납니다.

| 먼저 | 그다음 |
| --- | --- |
| `owner_token` 컬럼 추가 | AI 서버 배포 |
| `CHECKPOINT_DATABASE_URL` 설정 | AI 서버 배포 |
| `block` · `block_kind` 생성 + SELECT 권한 | 경험정리 flag 활성화 |
| 커밋·템플릿 API 배포 + 키 값 합의 | 경험정리 flag 활성화 |

---

## 7. 연결 검증

붙인 뒤 이 순서로 확인합니다. 앞 단계가 통과해야 뒤가 의미 있습니다.

### 7-1. 기동

- [ ] `/health` 가 `status: ok`
- [ ] `checkpointer: connected`, `experience_map_db: connected`
- [ ] `main_server: connected`

```bash
curl -s $AI_URL/health | python3 -m json.tool
```

### 7-2. 인증

- [ ] 메인 → AI `POST /sessions` 200
- [ ] 티켓으로 `GET /state` 200
- [ ] 만료 티켓 → `401 ticket_expired`
- [ ] 위조 서명 → `401 ticket_invalid`
- [ ] 다른 세션의 티켓 → `403 session_forbidden`
- [ ] 헤더 없음 → `401`
- [ ] AI → 메인 `GET /templates` 200 (키 값이 맞는지 확인되는 지점)

### 7-3. 데이터 접근

- [ ] 경험 맵이 있는 사용자로 `chat/stream` 이 맵을 읽어옴
- [ ] 맵이 없는 사용자 → `map_not_initialized`
- [ ] AI 계정으로 `block` 에 `INSERT` 가 **거부**되는지 (읽기 전용 확인)

### 7-4. 커밋

- [ ] 새 블록이 실제로 맵에 반영됨
- [ ] 같은 `request_id` 재호출 시 중복 반영되지 않음 (멱등)
- [ ] `map_version_conflict` 1회 복구 동작
- [ ] 커밋 성공 후 응답 유실 → `GET /commit/{request_id}` 로 복구

### 7-5. 동시성

- [ ] 같은 세션에 요청 2건 동시 → 1건만 성공, 나머지 409
- [ ] 실행 중 프로세스 강제 종료 → lease 만료 후 재시도 가능 상태로 풀림
- [ ] 그때 이미 커밋됐다면 실패가 아니라 완료로 복구되는지

---

## 8. 아직 명세에 없는 것

AI 서버가 추가했지만 명세 문서에 반영되지 않은 항목입니다.

| 항목 | 위치 | 비고 |
| --- | --- | --- |
| `owner_token` | 3-3 테이블 | 실행권 표식. 위 1-2 참고 |
| `lease_lost` | 6절 오류표 | 실행권 상실로 스트림이 끊긴 경우. **아직 오류표에 없음** |
| `429 rate_limited` | 2-3 | 티켓 `sub` 단위 분당 제한. `Retry-After` 포함 |
| `EXPMAP_RATE_LIMIT_PER_MINUTE` | 8절 | 기본값 20 |
| `EXPERIENCE_MAP_ENABLED` | 8절 | 기능 노출 flag |

---

## 9. 참고 — AI 서버 쪽 현재 상태

| 영역 | 상태 |
| --- | --- |
| API·SSE·티켓 인증·rate limit | ✅ |
| LLM 노드 전체와 그래프 배선 | ✅ |
| 커밋 위임·충돌 복구·gap 분석·재시도 | ✅ |
| 경험 맵 **읽기** | ⚠️ 코드는 명세 4-1 대로 있으나 **실제 테이블에 실행된 적 없음** |
| 템플릿 카탈로그 | ⚠️ 응답 구조를 **가정**하고 구현 |
| 시나리오 검증·flag 전환 | ⬜ 위 7절이 통과해야 착수 |
