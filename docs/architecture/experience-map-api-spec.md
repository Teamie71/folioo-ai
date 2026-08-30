# 경험정리 AI 서버 ↔︎ 메인 서버 API 연동 스펙

---

## 1. 아키텍처

```
                    ┌─────────────── PostgreSQL (경험 맵) ───────────────┐
                    │ 쓰기                                        읽기   │
                    │                                                    │
프론트 ──ticket──→ 메인 서버 ←──── 커밋·템플릿 API ──── AI 서버 ──────────┘
   │                                                     │
   └──────────────── SSE 직결 ────────────────────────────┤
                                                          ├→ OpenRouter
                                                          ├→ PostgreSQL (AI 세션·요청)
                                                          ├→ PostgreSQL (checkpoint)
                                                          └→ GCS (임시 첨부 파일)
```

| 서버 | 역할 |
| --- | --- |
| 메인 | 사용자 인증과 티켓 발급, **경험 맵 쓰기 전담**, 에디터 변경, 되돌리기, 템플릿 카탈로그 |
| AI | 에이전트 실행, **경험 맵 읽기**, 요청 상태 저장, 프론트와 SSE 직결 |

### 두 가지 원칙

**1. SSE는 프론트 ↔︎ AI 서버 직결.** 메인 서버는 스트림 경로에 없고, 시작 전
티켓 발급에만 관여합니다 (2-1). 중계 코드에 버퍼링 해제·순서 보존·커넥션 점유
비용을 지불하지 않습니다.

**2. 경험 맵 쓰기는 메인 서버 단독.** AI 서버는 읽기만 하고 반영은 커밋 API로
위임합니다 (7절). 이로써 다음이 해소됩니다.

- `map_version` 증가가 에디터·AI 커밋·되돌리기 세 경로에 흩어져 있던 것 → 한 구현체
- `ai_commit_log`를 AI가 쓰고 메인이 지우던 반쪽 소유권 → 메인 단독
- AI 서버가 자기 권한을 스스로 검증하던 것 → 메인이 심사

경험 맵 DB와 checkpoint DB는 반드시 분리합니다 (8절).

### 처리 흐름

```
파일 입력: Router → 파일처리 → 반영 내용 필터링 → 블록 구조화 → 문장 정제 → validate
채팅 입력: Router → 반영 내용 필터링 → 블록 구조화 → 문장 정제 → validate
gap 답변 : Router → 반영 내용 필터링 → 블록 구조화 또는 문장 정제 → validate
기능 밖  : Router → Fallback → 결과 응답

validate 성공 → 커밋 API 호출 ──────────→ 결과 응답
              └→ gap 분석 → 제안 생성 ──→ 제안 응답
```

gap 답변 여부는 Router가 아니라 반영 내용 필터링 노드가 판정합니다.
활성 gap은 `ai_experience_session.active_gap`에 저장돼 다음 턴까지 유지됩니다 (3-2).

커밋과 gap 분석은 동시에 시작합니다. AI 서버는 커밋 완료를 먼저 기다려 결과 응답을
보내고, 준비된 제안을 뒤이어 보냅니다. gap 분석 실패는 결과 응답에 영향을 주지 않습니다.

---

## 2. 공통 규칙

### 2-1. 인증

호출 주체에 따라 인증 방식이 셋입니다. AI 서버 API prefix는 `/api/v1/experience-map`.

| 호출 | 인증 | 대상 |
| --- | --- | --- |
| 메인 → AI | `X-API-Key: ${AI_SERVICE_API_KEY}` | `POST /sessions` |
| **프론트 → AI** | `Authorization: Bearer {ticket}` | `state`, `chat/stream`, `retry/stream`, `requests/{rid}` |
| AI → 메인 | `X-API-Key: ${AI_SERVICE_API_KEY}` | 커밋·템플릿 API (7절) |

#### 티켓 발급 흐름

```
① 프론트 → 메인   POST /api/v1/experience-map/ticket   (기존 로그인 인증)
② 메인            사용자 인증 → user_id 확보
                  세션 없으면 AI에 POST /sessions [X-API-Key]
                  request_id UUID 생성 → 티켓 서명
③ 메인 → 프론트   { ticket, session_id, request_id, expires_in }
④ 프론트 → AI     Authorization: Bearer {ticket}  → SSE 직결
```

티켓 payload:

```json
{
  "sub": "123",
  "sid": "d9428888-122b-11e1-b85c-61cd3cbb3210",
  "iat": 1754400000,
  "exp": 1754400300
}
```

**AI 서버 검증 3단계: 서명 → 만료 → `sid` == path `{session_id}`**

세 번째가 세션 탈취를 막습니다. `sub`로 `user_id`를 신뢰할 수 있으므로 AI 서버는
세션 테이블을 역조회하지 않고 바로 `block WHERE user_id = ?`를 사용합니다.

#### 티켓 운영 규칙

| 항목 | 규칙 |
| --- | --- |
| 서명 | HS256, 키는 `EXPMAP_TICKET_SECRET`. **`AI_SERVICE_API_KEY`를 재사용하지 않습니다** |
| TTL | 5분. **연결 수립 시점에만** 검사하고 스트림 진행 중에는 재검사하지 않습니다 |
| 단위 | 티켓 1개 = 한 턴. `request_id`를 발급 응답에 동봉합니다 (2-5) |
| 만료 시 재시도 | 재발급 후 **실패한 요청의 `request_id`를 그대로** 사용합니다 |
| 검증 시점 | **요청 body를 읽기 전** |

서명 키를 분리하는 이유는 두 키의 회전 주기가 다르고, API 키가 유출되면 티켓 위조까지
가능해지기 때문입니다.

TTL을 연결 시점에만 보는 이유는 파일처리 120초 + LLM 60초 + gap 30초로 스트림이
TTL보다 오래 살 수 있기 때문입니다. 재시도 TTL(30분)이 티켓 TTL(5분)보다 길어
재시도 시점에는 티켓이 만료돼 있는 것이 정상입니다.

body를 읽기 전에 검증하는 이유는 파일 업로드가 프론트 → AI 직결이라 최대 30MB가
직접 들어오기 때문입니다. 검증 전에 버퍼링하면 인증되지 않은 호출자가 그만큼
밀어넣을 수 있습니다.

#### 프론트 직결에 따른 AI 서버 책임

브라우저에 직접 노출되므로 아래가 AI 서버 몫이 됩니다.

- **CORS**: `ALLOWED_ORIGINS`에 웹 오리진 추가, `Authorization` 헤더 preflight 허용
- **rate limit**: 티켓 `sub`(사용자) 단위. 티켓 발급 자체의 제한은 메인 서버 책임
- **요청 크기 제한**: 파일 1개 × 10MB (5절)

### 2-2. 식별자

| 항목 | API 형식 | 저장 형식 |
| --- | --- | --- |
| `user_id` | 십진 문자열 | 메인 DB 사용자 PK 형식 |
| `experience_id` | 십진 문자열 | `block.id` (level 2 활동) |
| `block_id` | 십진 문자열 | `block.id` |
| `session_id` | UUID 문자열 | UUID |
| `request_id` | UUID 문자열 | UUID |

LLM에는 실제 block ID를 전달하지 않고 요청 안에서만 유효한 `exp_1`, `b_1` 별칭을
전달합니다. 별칭 ↔︎ 실제 ID 변환은 AI 서버가 수행합니다. 없는 별칭이 출력되면
그 항목을 탈락시켜 존재하지 않는 블록을 참조하는 사고를 구조적으로 막습니다.

### 2-3. 오류 응답

스트림 시작 전 오류는 JSON, 시작 후 오류는 `error` SSE 이벤트로 보냅니다.

```json
{
  "statusCode": 409,
  "code": "session_busy",
  "message": "다른 요청을 처리 중입니다."
}
```

| HTTP | `code` |
| --- | --- |
| `401` | `unauthorized` (X-API-Key 실패) |
| `401` | `ticket_invalid` (서명 불일치), `ticket_expired` |
| `403` | `session_forbidden` (티켓 `sid` ≠ path `session_id`) |
| `404` | `session_not_found`, `request_not_found`, `map_not_initialized` |
| `409` | `session_busy`, `idempotency_key_reused`, `retry_not_allowed` |
| `410` | `retry_expired` |
| `413` | `file_too_large` |
| `415` | `unsupported_file_type` |
| `422` | `invalid_request` |

`ApiKeyAuthMiddleware`와 티켓 검증 미들웨어 모두 위 JSON 형식을 사용합니다.

### 2-4. 재시도와 제한 시간

| 대상 | 처리 |
| --- | --- |
| Router·파일처리·filter·structure·refine | 실패한 노드만 1회 자동 재시도 |
| gap 분석·제안 생성 | 자동 재시도 없음, 실패 시 화면에 표시하지 않음 |
| validate 보정 | `structure` 또는 `refine`으로 최대 2회 회귀 |
| 커밋 API `5xx`·타임아웃 | 최대 3회, 1초·2초·3초 backoff |
| 커밋 API `4xx` | 재시도하지 않음 (`409 map_version_conflict`는 4-3의 재구성 경로) |
| SSE heartbeat | 10초마다 `ping` |
| 요청 실행 lease | 5분, 실행 중 30초마다 연장 |
| 사용자 재시도 | 실패 후 30분 |
| 텍스트 추출 실패 파일 | 업로드 후 1시간 |

OpenRouter 클라이언트의 내장 retry는 0으로 설정합니다. 자동 재시도 횟수는 LangGraph
`RetryPolicy` 한 곳에서만 관리합니다. 일반 LLM 노드는 60초, 파일처리는 120초,
gap 분석과 제안 생성은 각각 30초를 제한 시간으로 사용합니다.

### 2-5. 멱등성

- 메인 서버가 요청마다 UUID `request_id`를 생성해 티켓과 함께 내려줍니다.
프론트가 생성하면 멱등성 보장이 클라이언트로 넘어갑니다.
- AI 서버는 사용자 메시지, 화면 context, view, 파일 SHA-256을 정규화해
`request_hash`를 계산합니다.
- 같은 사용자의 동일 `request_id`와 동일 hash가 완료 상태면 저장 결과를 SSE로 재전송합니다.
- 동일 요청이 실행 중이면 새 stream을 붙이지 않고 `409 session_busy`를 반환합니다.
- 동일 요청이 실패 상태면 chat API가 아니라 retry API를 사용합니다.
- 동일 `request_id`와 다른 hash는 `409 idempotency_key_reused`입니다.
- 커밋 API도 `(user_id, request_id)` 기준으로 멱등합니다 (7절).
- 완료 요청은 30일 보관합니다.

---

## 3. DB 계약

메인 서버 마이그레이션으로 아래를 만듭니다. 사용자 FK의 테이블명과 PK 타입은
메인 서버의 실제 스키마를 따릅니다. 사용자 FK는 모두 `ON DELETE CASCADE`로 연결해
회원 탈퇴 시 세션·요청·되돌리기 기록을 함께 삭제합니다.

**기존 `block` 테이블에 `placeholder` 컬럼을 추가**해야 합니다 (3-7).

### 3-1. `experience_map`

```sql
CREATE TABLE experience_map (
  user_id      bigint PRIMARY KEY,
  map_version  bigint NOT NULL DEFAULT 1,
  updated_at   timestamptz NOT NULL DEFAULT now()
);
```

낙관적 잠금 대상이자 `SELECT ... FOR UPDATE`로 잠글 행입니다.
에디터 변경·AI 커밋·되돌리기 **세 경로 모두 메인 서버**가 수행하며, 각각 한
트랜잭션에서 `map_version`을 정확히 1씩 증가시킵니다.

### 3-2. `ai_experience_session`

```sql
CREATE TABLE ai_experience_session (
  user_id      bigint PRIMARY KEY,
  session_id   uuid NOT NULL UNIQUE,
  active_gap   jsonb,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, session_id)
);
```

사용자당 경험정리 세션은 1개입니다. LangGraph `thread_id`는 `session_id`,
`checkpoint_ns`는 `experience_map`을 사용합니다.

`active_gap`은 직전 턴의 gap 분석이 만든 제안을 다음 턴까지 유지합니다.
반영 내용 필터링 노드가 사용자 입력을 gap 답변으로 분류할 때 사용합니다.

```json
{
  "gap_id": "550e8400-e29b-41d4-a716-446655440000",
  "gap_type": "extend_block",
  "anchor_block_id": "3055",
  "message": "그 해결 방법을 고른 기준이 무엇이었나요?",
  "created_request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| `gap_type` | 사용자 답변 시 후속 노드 | 의미 |
| --- | --- | --- |
| `extend_block` | 문장 정제 | `anchor_block_id` 블록의 기존 내용에 답변을 결합 |
| `new_child_block` | 블록 단위 구조화 | `anchor_block_id` 하위에 새 블록 생성 |

gap이 해소되거나 새 gap이 생성되면 교체합니다. gap 없이 완료된 턴은 `null`로 만듭니다.

### 3-3. `ai_experience_request`

```sql
CREATE TABLE ai_experience_request (
  user_id             bigint NOT NULL,
  session_id          uuid NOT NULL,
  request_id          uuid NOT NULL,
  request_hash        char(64) NOT NULL,
  status              varchar(16) NOT NULL,
  failed_node         varchar(64),
  retryable           boolean NOT NULL DEFAULT false,
  retry_expires_at    timestamptz,
  lease_expires_at    timestamptz,
  base_map_version    bigint,
  committed_version   bigint,
  input_meta          jsonb NOT NULL DEFAULT '{}'::jsonb,
  result              jsonb,
  suggestion          jsonb,
  error               jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, request_id),
  FOREIGN KEY (user_id, session_id)
    REFERENCES ai_experience_session(user_id, session_id),
  CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE UNIQUE INDEX uq_ai_experience_request_running
  ON ai_experience_request(session_id)
  WHERE status = 'running';
```

**이 테이블이 세션·요청 상태의 유일한 기준입니다.** checkpoint에는 그래프 중간
산출물만 저장하며 API 상태를 checkpoint에서 계산하지 않습니다.

실행 시작 시 `running` 행을 원자적으로 생성합니다. `lease_expires_at`이 지난 요청은
`failed`로 전환한 뒤 새 요청 또는 사용자 재시도를 허용합니다 (4-3).

### 3-4. `ai_commit_log`

```sql
CREATE TABLE ai_commit_log (
  user_id           bigint PRIMARY KEY,
  request_id        uuid NOT NULL,
  previous_version  bigint NOT NULL,
  committed_version bigint NOT NULL,
  created_block_ids bigint[] NOT NULL,
  updated_blocks    jsonb NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now()
);
```

되돌리기용 **역연산 기록**입니다. 스냅샷 전체를 복원하지 않는 이유는 `block.id`가
`bigserial`이라 삭제 후 재삽입 시 PK가 바뀌고, `review`·`experience_meta`가 블록을
참조하므로 첨삭 데이터가 함께 사라지기 때문입니다.

사용자별 최신 1건만 저장하고 다음 커밋이 교체합니다. 24시간 동안만 되돌릴 수 있습니다.

**메인 서버 단독 소유입니다.** 커밋 API가 UPSERT하고, 에디터 변경으로 `map_version`을
올릴 때 함께 DELETE하며, 되돌리기가 사용 후 삭제합니다. 세 경로가 모두 메인 서버
안에 있습니다. 사용자가 블록을 하나라도 수정하면 되돌릴 수 없으므로 그 시점에
폐기하며, 24시간 TTL은 누락 대비 보조 정리입니다.

### 3-5. 소유권과 DB 권한

| 테이블 | 쓰기 | 읽기 |
| --- | --- | --- |
| `block` | 메인 | 메인, AI |
| `block_kind` | 메인 | 메인, AI |
| `experience_map` | 메인 | 메인, AI |
| `ai_commit_log` | 메인 | 메인 |
| `ai_experience_session` | AI | AI |
| `ai_experience_request` | AI | AI |

AI 서버 DB 계정 권한:

```
block, block_kind, experience_map              : SELECT
ai_experience_session, ai_experience_request   : SELECT, INSERT, UPDATE, DELETE
ai_commit_log                                  : 권한 없음
```

**AI 서버는 경험 맵을 조회만 하고 반영은 커밋 API로 위임합니다.** 조회는 API를
거치지 않고 읽기 전용 계정으로 직접 수행합니다(4-1) — 맵 전체를 매 요청 읽어야 해서
API로 감싸면 왕복만 늘어납니다.

DB 제약:

- `char_length(block.content) <= 500`
- block 위계는 1~5단계
- level 1만 `parent_id IS NULL`
- 자식 level은 부모 level + 1 (이 제약 하나로 깊이 초과와 순환이 동시에 막힙니다)
- `block_kind`의 고정 level 준수
- `is_text_editable=false` 블록 수정 금지

### 3-6. AI 에이전트의 블록 권한

위계별로 AI가 할 수 있는 작업이 다릅니다. **메인 서버가 커밋 API에서 검증합니다.**

| level | 이름 | 생성 | 수정 | 삭제 |
| --- | --- | --- | --- | --- |
| 1 | 그룹 | X | X | X |
| 2 | 활동 | X | X | X |
| 3 | 카테고리 | **O** | X | X |
| 4 | 항목 | O | O | X |
| 5 | 세부 항목 | O | O | X |

3단계 카테고리를 AI가 생성할 수 있는 경우는 둘입니다.

- 기본 제공 카테고리(상세정보·주요성과·담당업무·문제해결·배운 점) 중 해당 활동에
없는 것이 필요할 때 → 해당 `section_kind`로 생성
- 사용자 입력이 기본 카테고리 어디에도 맞지 않을 때 → level 3 `CONTENT`로 생성

**AI가 만드는 블록의 kind는 `CONTENT`와 `SECTION_*` 두 가지**이며, 어떤 위계의
블록도 삭제하지 않습니다. 3단계 카테고리는 생성만 하고 이름을 수정하지 않습니다.

### 3-7. placeholder와 템플릿 카탈로그

**템플릿 카탈로그는 메인 서버 단독 소유입니다.**

| 사용처 | 주체 |
| --- | --- |
| 신규 사용자 초기 데이터 생성 | 메인 |
| 커밋 시 블록 생성과 `placeholder` 부여 | 메인 |
| 템플릿 선택과 슬롯 구조 판단 | AI |

**AI는 문구가 아니라 `slot_id`를 보냅니다.**

```
AI가 보내는 것     : slot_id = "PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE"
메인이 저장하는 것 : placeholder = "문제의 원인은 무엇이었으며, ..."
```

- LLM이 템플릿 문구를 토씨까지 재생산할 필요가 없습니다
- 카탈로그 대조로 **검증이 가능**합니다. 없는 `slot_id`는 `422 unknown_slot_id`
- 문구가 바뀌어도 AI 서버 배포가 필요 없습니다

**`block.placeholder` 컬럼이 필요합니다.** 같은 level 4 `CONTENT`인데 상세정보·
담당업무·문제해결 하위의 문구가 전부 다르므로 `block_kind.placeholder`로는
표현할 수 없습니다. `slot_id`가 없는 블록(ad-hoc level 3 `CONTENT`, 자유 입력)은
`block.placeholder`도 `null`이며 `block_kind.placeholder` 폴백
(“내용을 입력해 주세요.”)이 적용됩니다.

카탈로그 조회는 `GET /templates`(7절)입니다. AI 서버는 **기동 시 1회 조회 후
1시간 TTL로 갱신**하고, `422 unknown_slot_id`를 받으면 즉시 재조회 후 1회 재시도합니다.

### 3-8. 빈 블록 생성 규칙

| 경우 | 규칙 |
| --- | --- |
| 템플릿을 쓰지 않는 생성 | 값이 들어갈 블록만 생성. 빈 블록을 만들지 않는다 |
| **템플릿을 쓰는 생성** | **템플릿의 모든 슬롯을 생성한다.** 채울 수 있는 블록에는 값을 넣고, 정보가 없는 블록은 `content` 없이 `slot_id`만 보낸다 |

문제해결 ‘기술 트러블슈팅’ 템플릿을 적용했는데 사용자가 5단계 4개 중 2개 분량만
이야기했다면 **4개를 모두 생성하고 2개만 채웁니다.** 나머지는 `content IS NULL`이라
화면에 placeholder가 보이고, gap 분석이 그 블록을 근거로 후속 질문을 만들 수 있습니다.

**빈 슬롯도 AI가 items에 포함해 보냅니다.** 메인이 템플릿을 전개해 주는 방식이 아니라
AI가 만들 블록을 모두 명시하는 방식입니다. items 외에 특수 명령이 없어 계약이
단순하고, AI가 어떤 슬롯을 만들지 완전히 제어합니다.

**이미 있는 빈 슬롯을 나중에 채우는 경우, `add`로 옆에 또 만들지 않고 그 슬롯
자신을 `update`합니다.** gap 분석은 커밋과 **병렬로** 실행되므로(1절), 이번 턴에
막 만드는 카테고리·앵커·빈 슬롯은 그 턴 안에서는 아직 실제 `block_id`가 없어
`extend_block`으로 정밀하게 가리킬 수 없습니다 — 그래서 다음 턴 사용자가 그 gap에
답하면 `new_child_block`으로 분류돼 블록 단위 구조화 노드로 전달됩니다. 이때
구조화 노드가 활동 트리에서 그 앵커 아래 **이미 있는 빈 슬롯**(예:
`TASK.BASIC.PURPOSE`, `content IS NULL`)과 정확히 같은 슬롯을 채우려는 것으로
판단되면, 같은 `slot_id`로 새 블록을 또 만들지 않고 그 빈 슬롯 자신을 `target_id`로
삼아 `update`로 채웁니다. 같은 (부모, `slot_id`) 조합이 두 번 생기는 것(하나는 빈
채로 방치되고 하나는 내용이 실리는 상태)을 막기 위한 예외이며, 그 외의 경우
구조화 노드는 계속 `add`만 만듭니다.

---

## 4. AI 서버의 처리

### 4-1. 경험 맵 조회

요청 시작, 사용자 재시도, version 충돌 복구 시 조회합니다.

```sql
SELECT b.id,
       b.parent_id,
       b.level,
       b.kind,
       b.position,
       b.content,
       COALESCE(b.placeholder, k.placeholder) AS placeholder,
       k.is_text_editable,
       k.is_deletable
  FROM block b
  JOIN block_kind k ON k.kind = b.kind
 WHERE b.user_id = $1
 ORDER BY b.level, b.parent_id, b.position, b.id;
```

`placeholder`는 블록별 값을 우선하고 없으면 kind 폴백을 씁니다 (3-7).

조회 결과로 트리를 만든 뒤 LLM에는 **전체 그룹·활동 outline과 선택한 활동의 전체
트리만** 전달합니다. 활동 50개를 매번 프롬프트에 넣으면 토큰이 감당되지 않고,
관계없는 활동이 섞이면 사실이 교차 오염됩니다. **한 요청은 한 활동만 수정합니다.**

빈 블록의 `content`와 `placeholder`는 분리해 전달합니다. 가이드 문구를 사용자
작성 내용으로 오인하는 것이 이 기능의 대표 실패 모드입니다.

**초기 데이터는 메인 서버가 생성합니다.** 신규 사용자가 처음 진입할 때
`experience_map` 행과 [경험정리 템플릿](https://app.notion.com/p/38f157eb55e280558721f316935e904f?pvs=21)
1절의 기본 제공 데이터(‘미분류’ 그룹, ’새로운 그룹 1 > 새로운 경험 1’과 5개 카테고리 및
placeholder 블록)를 만듭니다. AI 서버는 빈 맵을 만들지 않고 `map_not_initialized`로
처리를 중단합니다.

### 4-2. 커밋 items

`validate` 통과 직후 AI가 커밋 API(7절)로 보내는 items의 형태입니다.

```json
[
  {
    "item_id": "it_1",
    "action": "add",
    "parent_id": "3021",
    "parent_item_id": null,
    "section_kind": null,
    "slot_id": "PROBLEM_SOLVING.TROUBLESHOOTING.SUMMARY",
    "content": "결제 모듈 타임아웃으로 주문 실패율이 12%까지 올랐다.",
    "after_id": null
  },
  {
    "item_id": "it_2",
    "action": "update",
    "target_id": "3055",
    "content": "원인은 외부 PG사 응답 지연이었고 로그 분석으로 확인했다."
  }
]
```

| 필드 | 조건 |
| --- | --- |
| `item_id` | 요청 안에서 유일 |
| `action` | `add` 또는 `update` |
| `parent_id` | `add` 시 `parent_item_id`와 둘 중 하나 필수. 선택한 활동 내부 block |
| `parent_item_id` | 같은 요청에서 앞서 정의한 add item을 부모로 쓸 때 지정 |
| `section_kind` | level 3 카테고리 생성 시에만. 값은 아래 표 |
| `slot_id` | 템플릿 슬롯에 대응하면 지정. 문구는 메인이 카탈로그에서 부여 (3-7) |
| `target_id` | `update`에 필수. 선택한 활동 내부 editable block |
| `content` | 값이 있으면 공백 제외 1~500자. 카테고리 컨테이너와 템플릿 빈 슬롯은 생략 (3-8) |
| `after_id` | 같은 부모의 형제 block. null이면 형제 목록 끝에 추가 |

**`level`·`position`·`kind`·`placeholder`는 메인 서버가 계산합니다.** LLM도 AI 서버도
정하지 않습니다. `position` 재배치는 에디터 드래그 정렬과 같은 로직이어야 하므로
한 곳에 있어야 합니다.

`parent_item_id`는 items 배열에서 부모가 자식보다 먼저 나와야 합니다.

`kind`는 level이 3이면 `section_kind`(없으면 `CONTENT`), 4~5면 `CONTENT`입니다.
해당 활동에 같은 `section_kind`가 이미 있으면 거부합니다.

`section_kind` 값 — **API 전용 식별자이며 메인이 DB enum으로 매핑**합니다.

| 값 | 라벨 |
| --- | --- |
| `DETAIL` | 상세정보 |
| `ACHIEVEMENT` | 주요성과 |
| `TASK` | 담당업무 |
| `PROBLEM_SOLVING` | 문제해결 |
| `LEARNING` | 배운 점 |

DB enum 이름이 바뀌어도 API 계약이 깨지지 않습니다. `slot_id`가
`{SECTION}.{TEMPLATE}.{SLOT}` 형태라 카테고리와 슬롯이 같은 어휘 체계에 들어갑니다.

3단계 카테고리를 새로 만드는 item은 컨테이너이므로 `content`가 없습니다.
실제 내용은 그 아래 4단계 item이 `parent_item_id`로 참조해 담습니다.

### 4-3. 커밋 위임과 충돌 복구

```
validate 통과
→ AI: POST /api/v1/experience-map/commit  [X-API-Key]
→ 메인: 한 트랜잭션으로 검증·반영·version 증가·ai_commit_log UPSERT
→ AI: 응답을 ai_experience_request.result에 저장 → commit_result SSE
```

응답의 `previous_version`이 SSE `revert_to_version`이 되고, `can_revert`는 커밋
직후이므로 항상 `true`입니다.

**`409 map_version_conflict`를 받으면** 최신 맵을 조회합니다. 대상 block이 모두
유효하면 validate부터, 구조가 바뀌었으면 structure부터 한 번 재실행합니다.
두 번째 충돌은 `commit_conflict`로 실패 처리합니다.
**재구성 판단은 AI 서버가 하며 메인 서버는 재시도를 대행하지 않습니다.**

#### 커밋 성공 후 응답 유실

block 쓰기와 `ai_experience_request.result` 저장이 서로 다른 서비스에 있으므로
아래 상태가 생길 수 있습니다.

```
메인 커밋 성공 → 응답 유실 → AI가 result를 저장하지 못함
결과: ai_experience_request는 running, 실제 맵은 커밋됨
```

**만료된 lease를 정리할 때 `GET /commit/{request_id}`(7절)로 먼저 확인합니다.**

| 확인 결과 | 처리 |
| --- | --- |
| `committed: true` | 저장된 결과를 `result`에 채우고 `completed` |
| `committed: false` | `retryable` `failed` |

`GET /state`와 `GET /requests/{request_id}`가 만료 lease를 정리하는 시점에 이 확인을
수행합니다. lease가 살아 있으면 `running`을 그대로 반환하고 프론트는 폴링을
유지합니다. 재시도 버튼은 `failed`일 때만 노출합니다.

### 4-4. 첨부 파일

- API 서버는 파일을 GCS 임시 object로 업로드하고 object 경로와 SHA-256만 요청에 저장합니다.
- 텍스트 추출 성공 후 결과를 checkpoint에 저장하고 원본 object를 즉시 삭제합니다.
- 추출 노드가 **시스템 오류**로 실패하면 재시도를 위해 원본을 최대 1시간 유지합니다.
- 1시간이 지났고 추출 결과가 없으면 `retry_expired`를 반환합니다.
- 파일 품질 문제로 **추출 자체가 불가능한 경우는 노드 실패가 아니라 Fallback**입니다.
손상된 PDF에 재시도 버튼을 주면 몇 번을 눌러도 같은 결과가 나옵니다.
- 파일 본문과 추출 원문은 애플리케이션 로그에 남기지 않습니다.

---

## 5. AI 서버 API

**경로와 request/response 스키마는 종전과 같고 호출 주체와 인증만 바뀝니다.**

| 메서드 | 경로 | 호출자 | 인증 |
| --- | --- | --- | --- |
| `POST` | `/api/v1/experience-map/sessions` | 메인 서버 | `X-API-Key` |
| `GET` | `/api/v1/experience-map/sessions/{session_id}/state` | 프론트 | `Bearer` |
| `POST` | `/api/v1/experience-map/sessions/{session_id}/chat/stream` | 프론트 | `Bearer` |
| `POST` | `/api/v1/experience-map/sessions/{session_id}/retry/stream` | 프론트 | `Bearer` |
| `GET` | `/api/v1/experience-map/sessions/{session_id}/requests/{request_id}` | 프론트 | `Bearer` |

`POST /sessions`만 메인 서버가 호출합니다. 티켓 발급 과정에서 세션이 없을 때
먼저 만들기 위해서입니다 (2-1).

### `POST /sessions`

```json
{ "user_id": "123" }
```

**Response `201 Created` 또는 `200 OK`**

```json
{
  "session_id": "d9428888-122b-11e1-b85c-61cd3cbb3210",
  "status": "ready"
}
```

`status`는 `ready`, `running`, `failed` 중 하나입니다. 세션 생성만으로 LLM을
호출하지 않습니다.

### `GET /sessions/{session_id}/state`

화면 새로고침·재접속 시 호출합니다.

```json
{
  "session_id": "d9428888-122b-11e1-b85c-61cd3cbb3210",
  "status": "failed",
  "active_request_id": "550e8400-e29b-41d4-a716-446655440000",
  "retryable": true,
  "failed_node": "structure"
}
```

실행 중 요청 또는 마지막 요청으로 응답합니다. 만료된 running lease는 조회 전에
정리하며, 이때 4-3의 커밋 여부 확인을 수행합니다.

### `POST /sessions/{session_id}/chat/stream`

```
Content-Type: multipart/form-data
Accept: text/event-stream
```

| multipart part | 필수 | 설명 |
| --- | --- | --- |
| `request` | Y | 아래 JSON 문자열 |
| `files` | N | 첨부 파일 |

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "user_message": "결제 실패 문제를 해결한 내용을 정리해줘",
  "context_experience_id": "3021",
  "view": "map"
}
```

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `request_id` | Y | 티켓과 함께 받은 UUID |
| `user_message` | 조건부 | 파일이 없으면 필수 |
| `context_experience_id` | N | 현재 보고 있는 level 2 활동 block ID |
| `view` | N | `map`, `list`, null |

파일 제한:

| 처리 방식 | MIME | 확장자 |
| --- | --- | --- |
| 파일 파서 | `text/plain`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `application/vnd.openxmlformats-officedocument.presentationml.presentation` | `.txt`, `.docx`, `.pptx` |
| PDF 전 페이지 OCR | `application/pdf` | `.pdf` |
| OCR 모델 | `image/png`, `image/jpeg` | `.png`, `.jpg`, `.jpeg` |

개수 최대 1개, 파일당 최대 10MB.

MIME·확장자·실제 파일 signature를 모두 검사합니다. `.txt`는 signature가 없으므로
UTF-8 디코딩 성공 여부로 판정합니다. 메시지와 파일 중 하나 이상이 있어야 합니다.
PDF는 최대 10페이지까지 텍스트 레이어 유무와 관계없이 모든 페이지를 PNG로
렌더링해 페이지별 OCR을 수행합니다. 여러 페이지를 한 모델 요청에 몰아넣지 않으며
최대 3개 요청만 동시에 실행합니다. 한 요청에 파서 형식과 OCR 형식이 섞여 있으면
각각 처리한 뒤 입력 순서대로 이어 붙입니다.

동일 request가 이미 완료됐다면 새 graph를 실행하지 않고 저장한 `commit_result`,
결과 메시지, 제안, `processing_complete`를 같은 순서로 재전송합니다.

**Response `200 OK`**

```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

### `POST /sessions/{session_id}/retry/stream`

세션의 마지막 실패 요청을 checkpoint의 실패 노드부터 재실행합니다.

```json
{ "request_id": "550e8400-e29b-41d4-a716-446655440000" }
```

- 해당 요청이 세션의 마지막 요청이며 `failed`일 때만 허용합니다.
- 새 채팅 요청을 시작하면 이전 실패 요청은 더 이상 재시도할 수 없습니다.
- retry TTL과 추출 대기 파일 TTL을 각각 확인합니다.
- 재시도 직전에 경험 맵과 `map_version`을 다시 조회합니다.
- 티켓이 만료됐으면 프론트가 재발급받고 **같은 `request_id`로** 호출합니다 (2-1).

**Response `200 OK`**: SSE 스트림

### `GET /sessions/{session_id}/requests/{request_id}`

SSE 연결 종료·단절 뒤 요청 상태와 저장 결과를 복구합니다.

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "result": {
    "map_version": 43,
    "revert_to_version": 42,
    "can_revert": true,
    "applied": [],
    "dropped": []
  },
  "suggestion": null,
  "error": null
}
```

`status`는 `running`, `completed`, `failed` 중 하나입니다. 커밋이 끝난 직후에는
`running`이면서 `result`가 존재할 수 있습니다. 만료 lease 정리 시 4-3의 확인을
수행합니다.

---

## 6. SSE 이벤트

| 이벤트 | 설명 |
| --- | --- |
| `processing_started` | 요청 접수 완료 |
| `node_status` | 노드 시작·완료 |
| `commit_result` | 맵 반영 결과 |
| `message_complete` | 결과 또는 fallback 메시지 |
| `suggestion_ready` | gap 제안 |
| `processing_complete` | 요청 상태 저장 완료 |
| `error` | 요청 실패 |
| `ping` | heartbeat |

정상 커밋 이벤트 순서:

```
processing_started
→ node_status*
→ commit_result
→ message_complete(response_kind="result")
→ suggestion_ready
→ message_complete(response_kind="suggestion")
→ processing_complete
```

`message_complete`는 **메시지 단위 종료**이지 스트림 종료가 아닙니다. 결과 응답을
먼저 닫아 즉시 보여주고, gap 제안은 뒤이어 별도 메시지로 붙습니다.

gap 분석 또는 제안 생성이 **실패**했을 때만 `suggestion_ready`와 suggestion 메시지를
생략합니다. 분석에 성공했다면 **gap이 없어도 제안 메시지는 전송**합니다
(고정 문구 “더 정리하고 싶으신 내용이 있나요?”).

커밋 실패 시 실행 중인 gap 분석을 취소하고 `error`를 전송합니다.

### `processing_started`

```json
{
  "type": "processing_started",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### `node_status`

```json
{
  "type": "node_status",
  "node": "structure",
  "status": "running",
  "phrase": "경험 블록을 정리하고 있어요."
}
```

`node` 값: `router`, `file_processor`, `content_filter`, `gap_resolver`,
`structure`, `refine`, `validate`, `commit`.

`phrase`는 에이전트 문서 4절의 노드별 고정 문구다. `status: "running"`일 때만
채워지며, 문구가 없는 노드(`target_activity`·`gap_resolver`·`gap_analysis`·
`fallback` 등)와 `completed`/`failed` 상태에서는 `null`이다. Validation이
제한사항 미준수로 이전 노드를 되돌려 재실행시키는 경우, 그 재실행에는
`phrase`가 실리지 않는다 — 화면에는 Validation의 문구가 계속 보여야 하므로
클라이언트는 `phrase`가 `null`이면 마지막으로 받은 문구를 그대로 유지한다.

### `commit_result`

```json
{
  "type": "commit_result",
  "result": {
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "previous_version": 42,
    "map_version": 43,
    "revert_to_version": 42,
    "can_revert": true,
    "applied": [
      {
        "item_id": "it_1",
        "block_id": "3701",
        "path": "교내 커머스 리뉴얼 > 문제해결"
      }
    ],
    "dropped": [
      { "item_id": "it_9", "reason": "validation_retry_exceeded" }
    ]
  }
}
```

`applied`·`previous_version`·`map_version`은 커밋 API 응답을 그대로 옮긴 값입니다.
`revert_to_version`은 `previous_version`과 같고, `can_revert`는 커밋 직후이므로 `true`입니다.

**`dropped`는 AI 서버가 채웁니다.** validate 보정을 2회 초과한 항목은 커밋 요청
items에서 제외되므로 메인 서버는 그 존재를 모릅니다. 커밋 API 응답에는 `dropped`가
없습니다.

`path`는 결과 문구에 “어디에 넣었는지”를 보여주기 위해 필요합니다. 사전 승인이 없는
경로이므로 이 문구가 사용자가 오배정을 발견하는 주 경로입니다.

### `message_complete`

```json
{
  "type": "message_complete",
  "message": {
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "session_id": "d9428888-122b-11e1-b85c-61cd3cbb3210",
    "response_kind": "result",
    "ai_response": "교내 커머스 리뉴얼 > 문제해결에 블록 1개를 추가했습니다.",
    "committed": true,
    "map_version": 43,
    "can_revert": true
  }
}
```

`response_kind`는 `result`, `suggestion`, `fallback` 중 하나입니다.
Fallback은 `committed: false`이며 고정 문구 “아직 지원하지 않는 기능이에요.”를 보냅니다.

### `suggestion_ready`

**gap이 있을 때** — 한 응답에 최대 1개입니다.

```json
{
  "type": "suggestion_ready",
  "gap": {
    "gap_id": "550e8400-e29b-41d4-a716-446655440000",
    "gap_type": "extend_block",
    "anchor_block_id": "3055",
    "path": "교내 커머스 리뉴얼 > 문제해결",
    "message": "그 해결 방법을 고른 기준이 무엇이었나요?"
  }
}
```

**gap이 없을 때** — 분석은 성공했고 보완할 것이 없는 경우입니다.

```json
{ "type": "suggestion_ready", "gap": null }
```

`gap`이 있으면 `active_gap`에 저장해 다음 턴에서 사용하고, `null`이면 기존 값을
지웁니다 (3-2).

### `processing_complete`

```json
{
  "type": "processing_complete",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed"
}
```

### `error`

```json
{
  "type": "error",
  "error": {
    "code": "llm_error",
    "failed_node": "refine",
    "retryable": true,
    "message": "문장 정제에 실패했습니다."
  }
}
```

| `code` | 사용자 재시도 |
| --- | --- |
| `validation_failed` | 가능 |
| `commit_conflict` | 가능 |
| `llm_error` | 가능 |
| `node_timeout` | 가능 |
| `db_constraint_violation` | 불가 |

`retryable: true`일 때만 프론트가 재시도 버튼을 노출합니다. 재시도 요청에
`failed_node`를 되돌려 보낼 필요는 없습니다. 서버 checkpoint가 원본입니다.

---

## 7. 메인 서버 API

| 메서드 | 경로 | 호출자 | 인증 |
| --- | --- | --- | --- |
| `POST` | `/api/v1/experience-map/ticket` | 프론트 | 로그인 세션 |
| `POST` | `/api/v1/experience-map/commit` | AI 서버 | `X-API-Key` |
| `GET` | `/api/v1/experience-map/commit/{request_id}` | AI 서버 | `X-API-Key` |
| `GET` | `/api/v1/experience-map/templates` | AI 서버 | `X-API-Key` |
| `POST` | `/api/v1/experience-map/revert` | 프론트 | 로그인 세션 |

### `POST /ticket`

프론트가 AI 서버에 직결하기 전에 신원을 발급받습니다 (2-1).

**Response `200 OK`**

```json
{
  "ticket": "eyJhbGciOiJIUzI1NiJ9...",
  "session_id": "d9428888-122b-11e1-b85c-61cd3cbb3210",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "expires_in": 300
}
```

세션이 없으면 AI 서버 `POST /sessions`를 먼저 호출해 만든 뒤 발급합니다.

### `POST /commit`

**경험 맵 쓰기는 이 API 하나로 모입니다.**

```json
{
  "user_id": "123",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "base_map_version": 42,
  "items": []
}
```

`items` 스키마는 4-2와 같습니다. `user_id`는 AI 서버가 티켓 `sub`에서 얻은 값입니다.

**메인 서버가 수행하는 것**

```
BEGIN
  experience_map 행 SELECT FOR UPDATE
  이미 커밋된 request_id면 저장 결과 반환 (멱등)
  base_map_version 확인
  위계 권한 검증 (3-6)
  소유권·is_text_editable·부모·after 유효성 검증
  level·position·kind 계산, 형제 position 재배치
  slot_id → placeholder 문구 부여
  update 이전 content와 생성 block ID 수집
  block INSERT/UPDATE
  map_version + 1
  ai_commit_log UPSERT
COMMIT
```

**Response `200 OK`**

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "previous_version": 42,
  "map_version": 43,
  "applied": [
    { "item_id": "it_1", "block_id": "3701", "path": "교내 커머스 리뉴얼 > 문제해결" }
  ]
}
```

`(user_id, request_id)` 기준으로 멱등합니다. 이미 커밋된 요청은 재실행하지 않고
저장된 결과를 그대로 반환합니다.

**오류**

| HTTP | `code` | 의미 | AI 서버 처리 |
| --- | --- | --- | --- |
| `409` | `map_version_conflict` | `base_map_version` 불일치 | 4-3 재구성 |
| `409` | `request_id_reused` | 같은 `request_id`, 다른 items | `commit_conflict` |
| `422` | `invalid_hierarchy` | 위계 권한 위반 (3-6) | `db_constraint_violation` |
| `422` | `invalid_target` | 소유권·editable 위반 | `db_constraint_violation` |
| `422` | `unknown_slot_id` | 카탈로그에 없는 `slot_id` | 카탈로그 재조회 후 1회 재시도 |
| `404` | `map_not_initialized` | 초기 데이터 없음 | 동일 |

`map_version_conflict`는 **현재 버전을 함께 반환**합니다.

```json
{
  "statusCode": 409,
  "code": "map_version_conflict",
  "message": "맵이 변경되었습니다.",
  "current_map_version": 45
}
```

### `GET /commit/{request_id}`

크래시 복구용. AI 서버가 커밋 응답을 받기 전에 죽었을 때 커밋 여부를 확인합니다 (4-3).

```json
{
  "committed": true,
  "result": { "previous_version": 42, "map_version": 43, "applied": [] }
}
```

`committed: false`면 재커밋할 수 있습니다.

### `GET /templates`

템플릿 카탈로그 (3-7).

```json
{
  "version": "2026-08-05",
  "sections": [
    {
      "section_id": "PROBLEM_SOLVING",
      "label": "문제해결",
      "templates": [
        {
          "template_id": "TROUBLESHOOTING",
          "label": "기술 트러블슈팅",
          "slots": [
            {
              "slot_id": "PROBLEM_SOLVING.TROUBLESHOOTING.SUMMARY",
              "level": 4,
              "placeholder": "문제해결 에피소드를 한 줄로 요약해 주세요.",
              "example": "신규 프로모션 페이지 가입 이탈 문제 해결"
            },
            {
              "slot_id": "PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE",
              "level": 5,
              "placeholder": "문제의 원인은 무엇이었으며, 이를 파악하기 위해 어떤 검증 과정을 거쳤나요?",
              "example": "APM 툴로 병목 구간을 모니터링한 결과 ..."
            }
          ]
        }
      ]
    }
  ]
}
```

`example`은 노션 “예시 있는 버전 (AI용)”의 작성 예시입니다. AI가 프롬프트 few-shot과
정제 노드의 문체 기준으로 사용합니다.

카탈로그 본체를 코드 상수로 둘지 DB 테이블로 둘지는 메인 서버 내부 결정입니다.
응답 형태가 같으므로 AI 서버 구현에 영향이 없습니다.

### `POST /revert`

사용자가 결과 메시지의 되돌리기 버튼을 클릭할 때 실행합니다.
경로에 `user_id`가 없습니다 — 메인 서버가 세션에서 판별합니다.

```json
{ "request_id": "550e8400-e29b-41d4-a716-446655440000" }
```

처리:

```
BEGIN
  experience_map 행 SELECT FOR UPDATE
  인증 사용자와 request_id가 일치하는 최신 ai_commit_log 조회
  생성 후 24시간 이내인지 확인
  현재 map_version == committed_version 확인
  생성 block을 자식부터 삭제
  update block의 이전 content 복원
  map_version + 1
  ai_commit_log 삭제
COMMIT
```

**Response `200 OK`**

```json
{
  "map_version": 44,
  "reverted_request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

되돌리기도 하나의 변경이므로 버전은 **증가**합니다. 맵 내용만 이전 시점과 같아집니다.

| HTTP | 조건 |
| --- | --- |
| `404` | 사용자 경험 맵이 없음 |
| `409` | AI 커밋 뒤 다른 변경으로 version이 달라짐 |
| `410` | 최신 기록이 아니거나 24시간 만료 |

되돌리기 버튼은 프론트가 `현재 map_version == 커밋 응답의 map_version`으로 판정해
미리 비활성화하지만, 동시 편집으로 빠져나갈 수 있으므로 서버가 최종 판정합니다.

---

## 8. 환경변수

### AI 서버

| 변수 | 설명 |
| --- | --- |
| `DATABASE_URL` | 경험 맵·AI 세션·요청 DB |
| `CHECKPOINT_DATABASE_URL` | LangGraph checkpoint 전용 DB |
| `MAIN_BACKEND_URL` | 커밋·템플릿 API 호출 대상 |
| `AI_SERVICE_API_KEY` | 메인 ↔︎ AI 서버 간 인증 키 (양방향) |
| `EXPMAP_TICKET_SECRET` | 티켓 HS256 서명 키. `AI_SERVICE_API_KEY`와 별도 |
| `ALLOWED_ORIGINS` | 프론트 직결용 CORS 오리진 (기존 변수에 웹 오리진 추가) |
| `EXPMAP_UPLOAD_BUCKET` | 임시 첨부 파일 bucket |
| `EXPMAP_RETRY_TTL_SECONDS` | 기본값 `1800` |
| `EXPMAP_FILE_TTL_SECONDS` | 기본값 `3600` |
| `EXPMAP_REQUEST_LEASE_SECONDS` | 기본값 `300` |
| `EXPMAP_LLM_TIMEOUT_SECONDS` | 기본값 `60` |
| `EXPMAP_FILE_TIMEOUT_SECONDS` | 파일처리(파서·OCR) 기본값 `120` |
| `EXPMAP_GAP_TIMEOUT_SECONDS` | 기본값 `30` |

**`CHECKPOINT_DATABASE_URL`이 없으면 서버 시작을 실패시킵니다.** `DATABASE_URL`로
fallback하지 않습니다. 현재 `common/checkpointer/factory.py`가 폴백하도록 되어 있어
그대로 두면 checkpoint 테이블이 경험 맵 DB에 생성됩니다.

### 메인 서버

| 변수 | 설명 |
| --- | --- |
| `AI_SERVICE_URL` | AI 서버 Base URL |
| `AI_SERVICE_API_KEY` | AI 서버 호출 인증 키 |
| `EXPMAP_TICKET_SECRET` | 티켓 서명 키 (AI 서버와 공유) |
| `EXPMAP_TICKET_TTL_SECONDS` | 기본값 `300` |

---

## 9. 구현 목록

### 메인 서버

| 구분 | 항목 |
| --- | --- |
| DB | `experience_map`, `ai_experience_session`, `ai_experience_request`, `ai_commit_log` |
| DB | `block.placeholder` 컬럼 추가 |
| DB | AI 서버용 읽기 전용 계정 |
| DB | 에디터 변경 시 `map_version` 증가와 `ai_commit_log` 삭제 |
| API | `POST /ticket` |
| API | `POST /commit`, `GET /commit/{request_id}` |
| API | `GET /templates` |
| API | `POST /revert` |
| 로직 | 위계 권한·소유권·`is_text_editable` 검증 |
| 로직 | `level`·`position`·`kind` 계산, 형제 position 재배치 |
| 로직 | `slot_id` → placeholder 부여, `section_kind` → DB enum 매핑 |
| 로직 | 템플릿 카탈로그, 신규 사용자 초기 데이터 생성 |

### AI 서버

| 구분 | 항목 |
| --- | --- |
| 추가 | 티켓 검증 미들웨어, CORS, rate limit |
| 추가 | 커밋 API 클라이언트, `409`·`422` 처리, `GET /commit` 복구 조회 |
| 추가 | 템플릿 카탈로그 조회·캐시, `slot_id` 선택 |
| 유지 | 경험 맵 직접 조회(읽기), `ai_experience_*` 쓰기, 재구성 판단 |
| 유지 | 세션·요청 API 5개, LangGraph, 병렬 coordinator, SSE |

### 남은 결정

| 항목 | 주체 |
| --- | --- |
| 템플릿 카탈로그 본체를 코드 상수로 둘지 DB 테이블로 둘지 | 메인 서버 내부 |
| **`slot_id` 전체 목록,** 3단계 템플릿 10개 + 담당업무 4개 + 문제해결 6종 × 4개 = **약 38개 슬롯**의 ID를 확정해야함 |  |
- 템플릿 카탈로그
    
    ### 구조
    
    ```
    카테고리 슬롯 (level 4)   10개   ← 카테고리 생성 시 함께 전개
    하위 템플릿 (level 5)     28개   ← 담당업무 1종×4 + 문제해결 6종×4
                              38개
    ```
    
    담당업무·문제해결만 하위 템플릿을 가져. 나머지 셋은 level 4까지만.
    
    ### `slot_id` 형식
    
    ```
    level 4 : {SECTION}.{SLOT}              DETAIL.MOTIVATION
    level 5 : {SECTION}.{TEMPLATE}.{SLOT}   PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE
    ```
    
    점 개수가 곧 level. 스펙 3-7이 3-part만 언급하는데 정정 필요해.
    
    ### 카테고리 슬롯 (level 4, 10개)
    
    | `section_kind` | `slot_id` | 하위 템플릿 |
    | --- | --- | --- |
    | `DETAIL` | `DETAIL.MOTIVATION` `DETAIL.PERIOD` `DETAIL.ROLE` `DETAIL.TARGET` `DETAIL.STACK` | 없음 |
    | `ACHIEVEMENT` | `ACHIEVEMENT.QUANTITATIVE` `ACHIEVEMENT.QUALITATIVE` | 없음 |
    | `TASK` | `TASK.SUMMARY` | **앵커** |
    | `PROBLEM_SOLVING` | `PROBLEM_SOLVING.SUMMARY` | **앵커** |
    | `LEARNING` | `LEARNING.GROWTH` | 없음 |
    
    ### 하위 템플릿 (level 5, 28개)
    
    | 섹션 | `template_id` | 라벨 | 슬롯 |
    | --- | --- | --- | --- |
    | TASK | `BASIC` | 기본 | `PURPOSE` `RESEARCH` `EXECUTION` `RESULT` |
    | PROBLEM_SOLVING | `BASIC` | 기본 | `PROBLEM` `CAUSE` `SOLUTION` `RESULT` |
    | PROBLEM_SOLVING | `INTERPERSONAL` | 대인관계 | `SITUATION` `ACTION` `OUTCOME` `LEARNING` |
    | PROBLEM_SOLVING | `PERFORMANCE` | 성과 부진 개선 | `METRIC` `CAUSE` `ACTION` `RESULT` |
    | PROBLEM_SOLVING | `TROUBLESHOOTING` | 기술 트러블슈팅 | `PROBLEM` `CAUSE` `SOLUTION` `VERIFICATION` |
    | PROBLEM_SOLVING | `FEEDBACK` | 피드백 대응 | `RECEIVED` `NEED` `ACTION` `OUTCOME` |
    | PROBLEM_SOLVING | `RECOVERY` | 실패 회복 | `FAILURE` `CAUSE` `EFFORT` `CHANGE` |
    
    전체 ID는 `{섹션}.{template_id}.{슬롯}` — 예: `PROBLEM_SOLVING.RECOVERY.EFFORT`
    
    ### AI가 지켜야 할 규칙
    
    **앵커 구조.** level 5는 반드시 앵커 슬롯(`TASK.SUMMARY` / `PROBLEM_SOLVING.SUMMARY`)으로 만든 level 4 블록 아래에 붙어. items에서 `parent_item_id`로 그 앵커를 참조하거나, 기존 블록이면 `parent_id`로.
    
    **두 가지 사용 경로.**
    
    | 경우 | items 구성 |
    | --- | --- |
    | 새 업무/에피소드 | 앵커 level 4 + 하위 템플릿 level 5 전체 |
    | 기존 4단계 아래 보강 | 하위 템플릿 level 5만 |
    
    **빈 슬롯도 보낸다** (스펙 3-8). 템플릿을 쓰면 4개 중 2개만 채워도 4개 다 items에 넣고, 나머지는 `content` 없이 `slot_id`만.
    
    **반복 가능.** 담당업무는 업무 하나당, 문제해결은 에피소드 하나당 한 벌. 한 활동에 여러 벌 가능.
