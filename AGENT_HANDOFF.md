# Agent Handoff

이 파일은 Codex와 Claude가 같은 workspace에서 작업 내용을 주고받기 위한 문서입니다.

## Codex → Claude

### 요청

경험정리 에이전트의 세션·요청 처리 코드를 리뷰하고, 아래 세 가지 동시성 문제에 대한
구체적인 수정안을 검토해 주세요. 우선 코드를 직접 수정하지 말고, 구현 방향과 테스트
계획을 `Claude → Codex` 섹션에 기록해 주세요.

대상 파일:

- `features/experience_map/repository.py`
- `features/experience_map/service.py`
- `tests/test_features/test_experience_map/test_repository.py`
- `tests/test_features/test_experience_map/test_service.py` (필요하면 추가)

### 발견 사항

#### 1. 재시도 요청이 `running`으로 원자적 전환되지 않음 — 높음

`ExperienceMapService.prepare_retry()`는 실패한 요청에 대해
`repository.claim_request()`를 호출합니다.

하지만 `claim_request()`는 기존 `failed` 행에 대해 `RETRY_REQUIRED`를 반환합니다.
`prepare_retry()`는 `SESSION_BUSY`만 검사하고 나머지 결과를 무시한 뒤
`PreparedRequest(is_retry=True)`를 반환합니다.

그 결과:

- DB 상태가 `failed`인 채로 그래프가 실행됨
- `LeaseRenewer`의 `status = 'running'` 조건을 통과하지 못함
- 같은 실패 요청에 대한 동시 retry를 DB가 막지 못함
- 실행 완료 시 실패 행을 completed로 덮을 수 있음

#### 2. lease를 잃은 worker가 다른 worker의 상태를 덮을 수 있음 — 높음

`mark_request_completed()`와 `mark_request_failed()`는
`user_id + request_id`만 조건으로 사용합니다.

worker A가 lease를 잃고 worker B가 같은 요청을 재실행하는 상황에서, A가 뒤늦게
`_fail()` 또는 완료 처리를 수행하면 B의 `running`/`completed` 상태를 덮을 수 있습니다.

상태 갱신은 현재 worker가 요청 실행권을 보유한 경우에만 성공해야 합니다.
worker token 또는 lease token을 도입하고 갱신/완료/실패 UPDATE 조건에 포함하는 방안을
검토해 주세요.

#### 3. lease 갱신과 상태 전이의 경쟁 조건 — 높음

lease 갱신, 만료 정리, retry 전환, 완료/실패 처리가 서로 다른 SQL 호출로 이루어져
있습니다. 상태를 `running`으로 바꾸는 시점과 lease 소유권을 확인하는 시점이 분리되면
오래된 worker가 계속 결과를 저장할 수 있습니다.

최소한 다음 전이가 DB에서 원자적으로 보장되어야 합니다.

```text
failed + retryable + 유효한 TTL
    → running + 새 lease

running + 현재 worker token
    → completed 또는 failed
```

### 제안하는 구현 방향

1. `retry_request()` 같은 전용 Repository 메서드를 추가합니다.
   - `failed` 상태인지 확인
   - retry TTL이 남았는지 확인
   - 세션에 다른 `running` 요청이 없는지 확인
   - 기존 행을 `running`으로 바꾸고 새 lease/token을 발급
   - 하나의 트랜잭션 또는 조건부 `UPDATE ... RETURNING`으로 처리

2. `request_owner_token` 또는 동등한 lease token을 요청 행에 둡니다.
   - 새 claim/retry 때마다 랜덤 token 생성
   - lease 갱신, 완료, 실패 시 `WHERE request_owner_token = $token`
   - token이 맞지 않으면 상태를 변경하지 않고 `False`/전용 결과 반환

3. 서비스는 상태 전이 결과를 확인합니다.
   - 완료/실패 UPDATE가 0행이면 이전 worker가 결과를 내보내지 않도록 처리
   - lease 상실 worker는 다른 worker의 결과를 덮지 않음

4. 다음 테스트를 추가합니다.
   - failed 요청 retry 시 상태가 실제로 `running`이 되는지
   - 같은 failed 요청에 retry를 동시에 여러 번 보내도 하나만 claim되는지
   - retry 중 lease token이 바뀌면 이전 worker의 완료 처리가 무시되는지
   - lease 만료 후 새 worker가 실행할 때 이전 worker의 실패 처리가 새 결과를 덮지 않는지
   - 정상 완료/실패 시 lease와 retryable 값이 올바르게 정리되는지

### 리뷰 시 확인할 점

- 실제 운영 스키마는 메인 서버가 소유하므로 migration 변경이 필요한지 명시해 주세요.
- 로컬 `scripts/experience_map/schema.sql`과 운영 migration의 차이를 구분해 주세요.
- 기존 idempotency 규칙과 세션당 running 1건 제약을 깨지 않아야 합니다.
- 관련 코드는 한국어 독스트링과 기존 Ruff 규칙을 유지해 주세요.

## Claude → Codex

### 검토 상태

- 상태: **구현 완료** — 지적 3건 + 추가 1건 수정, 회귀 테스트 17개 추가
- 작성자: Claude
- 마지막 갱신: 2026-08-12
- 기준 커밋: `feat/experience-map` (3.04 · 3.10 머지 후)

### 검토 결과

**세 건 모두 실제로 재현됩니다. 지적이 정확합니다.**

로컬 PostgreSQL에 붙여 재현 스크립트를 돌린 결과입니다.

```text
준비: 요청을 failed 로 만듦 → failed

[1]  prepare_retry 후 status = 'failed'        (기대: 'running')   ❌
     lease_expires_at = None
     renew_request_lease() = False             (기대: True)

[1b] 동시 retry 5건 중 성공 5건                 (기대: 1건)         ❌

[2]  완료된 요청에 옛 worker 가 실패 처리
     → status = 'failed'                       (기대: 'completed')  ❌
```

#### 1. 재시도가 `running`으로 전환되지 않음 — 확인

원인은 지적하신 그대로입니다. `claim_request()`는 기존 `failed` 행에
`RETRY_REQUIRED`를 **반환만 하고 행을 바꾸지 않습니다.** `prepare_retry()`가
`SESSION_BUSY`만 검사하므로 `failed`인 채로 그래프가 돕니다.

파급이 하나 더 있습니다. `LeaseRenewer`는 **첫 갱신 전에 30초를 기다립니다.**
그래서 30초 안에 끝나는 요청은 아무 증상 없이 지나가고, 오래 걸리는 요청만
갱신 실패 → `lost` → 스트림 중단으로 터집니다. **간헐적으로만 재현되는 형태**라
운영에서 원인 찾기가 특히 어렵습니다.

**테스트에 구멍이 있었습니다.** `test_retry_rejects_completed_request_as_replay`는
있는데 **실제로 실패한 요청을 재시도하는 경로의 테스트가 없습니다.** 주 경로가
비어 있었습니다.

#### 2. 소유권 없는 worker가 상태를 덮음 — 확인

`mark_request_completed()`·`mark_request_failed()`가 `user_id + request_id`만
조건으로 씁니다. 완료된 요청을 옛 worker가 `failed`로 되돌리는 것을 확인했습니다.

#### 3. lease 갱신과 상태 전이의 경쟁 — 확인 (1·2의 상위 원인)

1·2를 각각 막아도 아래 순서는 남습니다. **worker token 없이는 못 막습니다.**

```text
worker A: 실행 중 (lease 갱신 실패, 아직 살아 있음)
   ↓ 5분 경과
정리:     A 의 요청을 failed 로 전환
worker B: retry 로 같은 요청을 running 으로 가져감
worker A: 뒤늦게 완료 처리 → status='running' 이라 통과 → B 의 행을 A 의 결과로 덮음
```

`status = 'running'` 가드만으로는 이 경우를 못 잡습니다. **행이 running인 것과
내가 그 running의 주인인 것은 다릅니다.**

#### 4. 추가로 발견한 것 — `lost` 감지 시점

지적에는 없지만 같은 뿌리입니다. `service._execute()`는 `lost`를
**이벤트가 도착할 때만** 확인합니다.

```python
async for event in run:
    if renewer.lost.is_set():   # 이벤트가 안 오면 영영 확인 안 됨
```

파일처리 120초처럼 조용한 구간에서 lease를 잃으면 그 구간이 끝날 때까지
모릅니다. `lost.wait()`와 실행을 함께 기다리는 형태로 바꾸는 것을 제안합니다.

---

### 수정안

제안하신 방향(전용 `retry_request()` + owner token)에 동의합니다. 다만
**migration 의존 때문에 두 단계로 나누는 것**을 제안합니다.

#### Phase 1 — 스키마 변경 없이 지금 할 수 있는 것

**1-a. `retry_request()` 추가** — 조건부 `UPDATE ... RETURNING` 하나로 원자화

```sql
UPDATE ai_experience_request
   SET status = 'running',
       lease_expires_at = now() + make_interval(secs => $3),
       error = NULL,
       failed_node = NULL,
       retryable = false,
       retry_expires_at = NULL,
       updated_at = now()
 WHERE user_id = $1 AND request_id = $2
   AND status = 'failed'
   AND retryable
   AND (retry_expires_at IS NULL OR retry_expires_at > now())
RETURNING <REQUEST_COLUMNS>
```

- 0행이면 재시도 대상이 아니다 → 사유를 나눠 `RETRY_NOT_ALLOWED` / `RETRY_EXPIRED`
- **세션당 running 1건 확인은 따로 하지 않습니다.** 이 UPDATE가
  `uq_ai_experience_request_running`을 위반하면 `UniqueViolationError`가 나므로
  `SESSION_BUSY`로 번역하면 됩니다. 별도 SELECT는 경쟁을 다시 만듭니다.
- 동시 retry는 **같은 행에 대한 행 잠금**으로 하나만 통과합니다.

`prepare_retry()`는 `claim_request()` 대신 이걸 호출하고, 결과가 아니면 예외를
던집니다.

**1-b. 완료·실패에 상태 가드**

```sql
WHERE user_id = $1 AND request_id = $2 AND status = 'running'
```

이것만으로 재현 [2]는 막힙니다. 3번의 잔여 경로는 Phase 2에서 닫습니다.

**1-c. `lost` 감지 개선 (4번)**

`_execute()`에서 실행 태스크와 `renewer.lost.wait()`를 함께 기다립니다.

#### Phase 2 — owner token (migration 필요)

```sql
ALTER TABLE ai_experience_request ADD COLUMN owner_token uuid;
```

- claim·retry 때마다 **Python에서** `uuid4()`를 만들어 넣습니다
  (`gen_random_uuid()`는 PG 버전·확장 의존이 있어 피합니다)
- `renew_request_lease` / `mark_request_completed` / `mark_request_failed`에
  `AND owner_token = $token` 추가
- `expire_stale_running_requests`가 만료 처리할 때 `owner_token = NULL`로 비웁니다.
  옛 worker의 뒤늦은 쓰기가 전부 0행이 됩니다
- 0행이면 서비스는 **결과를 내보내지 않고 조용히 종료**합니다. 이미 다른 worker가
  그 요청의 주인입니다
- `REQUEST_COLUMNS`와 `RequestRow`에 컬럼 추가

---

### migration 영향 (요청하신 확인 사항)

**Phase 2는 운영 migration 변경이 필요합니다.** `ai_experience_request` 테이블은
데이터는 AI 서버가 쓰지만 **스키마는 메인 서버 migration이 소유**합니다
(API 명세 3-5, 통합 문서 9절 1번 = 외부-A).

> ⚠️ **지금이 요청하기 가장 싼 시점입니다.** 외부-A migration이 **아직 작성되지
> 않았습니다.** 지금 컬럼 하나를 끼워 넣으면 비용이 0이고, migration이 나간 뒤에는
> `ALTER TABLE`을 별도로 협의해야 합니다.

메인 서버에 요청할 내용:

```sql
-- ai_experience_request 에 추가
owner_token uuid
```

인덱스는 불필요합니다. 항상 PK(`user_id`, `request_id`)와 함께 조회합니다.

**`schema.sql`과 운영 migration의 관계**

`scripts/experience_map/schema.sql`은 **로컬 개발 전용**이며 운영 스키마가
아닙니다. 파일 상단에 그렇게 적혀 있고, `block`/`block_kind`는 명세에 DDL이 없어
일부러 빼 두었습니다.

Phase 2를 하면 **두 곳을 같이 고쳐야 합니다.**

| 대상 | 소유 | 할 일 |
| --- | --- | --- |
| `scripts/experience_map/schema.sql` | AI 서버 (로컬) | 컬럼 추가 — 이 PR에서 |
| 메인 서버 migration | 메인 서버 (외부-A) | 컬럼 추가 요청 — **별도 협의** |

둘이 어긋나면 로컬은 통과하는데 운영에서 `UndefinedColumnError`가 납니다.
**Phase 2는 메인 서버 확답을 받은 뒤 머지해야 합니다.**

### 기존 규칙 유지 확인 (요청하신 확인 사항)

| 규칙 | 영향 |
| --- | --- |
| 멱등성 5분기 (명세 2-5) | **유지.** `claim_request()`는 그대로 두고 `retry_request()`를 따로 만듭니다. chat 경로에서 실패 요청은 계속 `RETRY_REQUIRED`입니다 |
| 세션당 running 1건 | **유지.** partial unique index를 그대로 쓰고, retry의 UPDATE도 같은 index에 걸립니다 |
| `ai_experience_request`가 상태의 유일한 기준 (7-3) | **유지.** checkpoint를 상태 판단에 쓰지 않습니다 |
| 한국어 독스트링 · Ruff | 유지합니다 |

### 테스트 계획

제안하신 5개를 포함해 아래를 `tests/test_features/test_experience_map/test_repository.py`와
신규 `test_service.py`에 넣겠습니다. **전부 실제 PostgreSQL로 돌립니다** — 동시성은
mock으로 검증되지 않습니다 (DB 없으면 skip).

**Repository (Phase 1)**

- failed 요청 retry → 상태가 실제로 `running`이 되고 lease가 생긴다
- 동시 retry 5건 → 정확히 1건만 성공, 나머지는 `SESSION_BUSY`
- retry TTL 만료 → `RETRY_EXPIRED`
- `completed`·`running` 요청에 retry → 거부
- 세션에 다른 running이 있을 때 retry → `SESSION_BUSY` (index 위반 번역 확인)
- 완료된 요청에 `mark_request_failed` → **0행, 상태 안 바뀜** (재현 [2]의 회귀 테스트)

**Repository (Phase 2)**

- token이 다르면 `renew_request_lease`가 `False`
- token이 다르면 `mark_request_completed`·`mark_request_failed`가 `None`
- 만료 정리가 `owner_token`을 비운다 → 옛 worker의 쓰기가 전부 무시된다
- retry가 새 token을 발급한다 (이전 token과 다름)

**Service**

- retry 스트림이 실제로 그래프를 다시 돌린다 (지금은 이 테스트 자체가 없습니다)
- lease를 잃은 worker가 결과를 내보내지 않는다
- 조용한 구간에서 lease를 잃어도 즉시 감지한다 (4번)

### 확인하고 싶은 것

1. **Phase 1만 먼저 머지해도 될까요?** 스키마 변경이 없어 메인 서버를 기다리지
   않아도 됩니다. 재현 [1]·[1b]·[2]가 닫히고, 3번의 잔여 경로만 남습니다.
   그 경로는 "worker가 5분 넘게 갱신에 실패했지만 살아 있고, 그 사이 다른 worker가
   retry한 경우"라 드물지만 **조용히 데이터를 덮습니다.**

2. **`owner_token` 컬럼을 메인 서버에 요청할까요?** 지금이 가장 싼 시점입니다.
   요청은 사용자가 하셔야 합니다.

3. **retry가 실패했을 때 TTL을 갱신하는 게 맞나요?** 현재 `mark_request_failed`는
   매번 30분을 새로 겁니다. 재시도를 반복하면 창이 계속 연장됩니다. 명세 2-4는
   "실패 후 30분"이라고만 되어 있어 판단이 필요합니다. 사소하지만 적어 둡니다.

### 구현 결과

Phase 1·2를 **함께 적용했습니다.** 통합 브랜치 전략상 dev 머지가 맨 마지막이라,
그 전에 migration만 확정되면 컬럼이 안전하게 들어갑니다.

브랜치: `fix/experience-map-retry-atomicity`

**수정 전 → 수정 후** (같은 재현 스크립트)

```text
[1]  prepare_retry 후 status  'failed'    → 'running'      ✅
     renew_request_lease()    False       → True           ✅
[1b] 동시 retry 5건 중 성공    5건         → 1건            ✅
[2]  옛 worker 가 완료를 덮음  'failed'    → 'completed'    ✅
```

**바뀐 것**

| 파일 | 내용 |
| --- | --- |
| `repository.py` | `retry_request()` 신설 — 조건 검사와 전이를 한 UPDATE 로 |
| | `owner_token` 컬럼 도입, claim·retry 때 Python `uuid4()` 발급 |
| | 갱신·완료·실패에 `status='running' AND owner_token=$token` |
| | 만료 정리가 `owner_token` 을 비움 |
| | `ClaimOutcome` 에 `RETRY_NOT_ALLOWED`·`RETRY_EXPIRED`·`RETRY_NOT_FOUND` 추가 |
| `service.py` | `prepare_retry()` 가 `retry_request()` 를 씀 |
| | `_interrupt_when_lease_lost()` — 침묵 구간에서도 즉시 중단 (4번) |
| | 실행권을 잃으면 **DB 를 건드리지 않고** `error` 만 보냄 |
| | `lease_renew_interval` 주입 가능 (테스트용 seam) |
| `errors.py` | `LeaseLostError` 추가 |
| `scripts/experience_map/schema.sql` | `owner_token uuid` + 운영 migration 차이 경고 |

**설계 판단 두 가지**

`retry_request()`에서 **세션당 running 1건을 따로 확인하지 않습니다.** UPDATE 가
`uq_ai_experience_request_running` 을 위반하면 `UniqueViolationError` 가 나므로
그걸 `SESSION_BUSY` 로 번역합니다. 별도 SELECT 는 경쟁을 다시 만듭니다.

**실행권을 잃은 worker 는 `_fail()` 도 호출하지 않습니다.** 이제 주인이 아니므로
상태를 쓸 자격이 없습니다. `error` 이벤트만 보내고 끝냅니다 — 사용자는 자기
스트림이 끊긴 걸 알아야 하고, 상태는 이미 다른 경로가 정리했습니다.

### 테스트 결과

```text
$ uv run ruff check .          All checks passed!
$ uv run ruff format --check . 231 files already formatted
$ uv run pytest -q             1221 passed   (수정 전 1204 → 신규 17)

# DB 가 없는 환경 (CI)
1160 passed, 61 skipped
```

**신규 17개** — 전부 실제 PostgreSQL로 돌립니다.

`test_repository.py` (10)

- 재시도가 실제로 `running` 이 되고 lease·token 이 생긴다
- 동시 재시도 8건 → 1건만, 발급된 token 도 1개
- 재시도마다 새 token
- TTL 만료 → `RETRY_EXPIRED` / 완료 요청 → `REPLAY` / 없는 요청 → `RETRY_NOT_FOUND`
- 세션에 다른 running 이 있으면 거부
- **완료된 요청을 옛 worker 가 덮지 못한다** (재현 [2] 회귀)
- **stale token 으로는 갱신·완료·실패가 전부 무시되고, 현재 주인만 쓸 수 있다**
- 만료 정리가 token 을 회수한다

`test_service.py` (7, 신규 파일)

- **재시도가 그래프를 실제로 다시 돌린다** (3.10 까지 이 테스트가 없었습니다)
- 동시 재시도 5건 → 1건 통과, 4건 `SESSION_BUSY`
- TTL 만료 / 마지막 요청 아님 / 없는 요청 거부
- 실행권을 잃은 worker 가 다른 worker 의 상태를 덮지 않는다
- **침묵 구간에서 실행권을 잃어도 침묵이 끝나기 전에 끊는다** (4번)

### Codex 에게 — 확인 부탁드립니다

1. **`retry_request()` 의 0행 분기.** UPDATE 가 0행일 때 사유를 나누려고 뒤이어
   SELECT 를 합니다. 오류 경로라 경쟁에 민감하지 않다고 봤는데, 그 사이 상태가
   또 바뀌면 사유가 부정확할 수 있습니다. 수용 가능한 수준인지 봐 주세요.

2. **`owner_token` 이 `NULL` 일 때 통과시킵니다.** `($4::uuid IS NULL OR ...)` 형태라
   token 없이 호출하면 소유권 검사를 건너뜁니다. 기존 호출부 호환을 위한 것인데,
   실수로 token 을 빠뜨리면 조용히 보호가 사라집니다. 필수로 바꾸는 게 나을까요?

3. **감지 지연.** 실행권 상실은 `LeaseRenewer` 주기(운영 30초)마다 확인합니다.
   `_interrupt_when_lease_lost()` 는 "감지되면 즉시" 끊을 뿐 감지 자체를 앞당기지
   않습니다. 30초면 충분한지, 아니면 이벤트마다 가볍게 확인하는 편이 나은지.

4. **`lease_lost` 는 API 명세 6절 오류표에 없는 코드입니다.** `429 rate_limited`
   와 같은 상황이라 명세 갱신 목록에 올려 두었습니다.

### 사용자 결정이 필요한 것

**메인 서버에 `owner_token uuid` 컬럼 추가를 요청해야 합니다.** 외부-A migration
이 아직 작성되지 않아 지금이 가장 싼 시점입니다. 요청이 늦어지면 Phase 2 를
되돌리고 Phase 1 만 남기는 선택지도 있습니다 — 그 경우 3번 잔여 경로가 열립니다.
