# 계약 변경 제안 — 결정 사항

> 대상 제안: 「계약 변경 제안」(SSE 구조 변경 + DB 소유권)
> 반영 문서: [API 명세](experience-map-api-spec.md), [개발 플랜](experience-map-agent.md)

## 결론

**두 제안 모두 수용합니다.** 제안서에서 명시되지 않았던 8개 항목은 아래와 같이
정하고 문서에 반영했습니다.

특히 DB 소유권 제안의 세 지적은 모두 기존 설계의 실제 결함이었습니다.

- `ai_commit_log` 소유권이 AI(UPSERT)와 메인(DELETE)으로 쪼개져 있던 것
- `map_version` 증가가 세 경로에 흩어졌는데 구현체가 둘이던 것
- **AI 서버가 자기 권한을 스스로 검증하던 것** — 심사 주체와 피심사 주체가 같았음

`slot_id` 체계도 기존 `placeholder` 문구 전달 방식보다 낫습니다. LLM이 템플릿 문구를
토씨까지 재생산할 필요가 없어지고, 카탈로그 대조로 검증이 가능해집니다.

---

## 1. SSE 구조 변경 — 수용

제안대로 프론트 ↔ AI 서버 SSE 직결, 메인 서버는 티켓 발급만 담당합니다.
경로와 request/response 스키마는 변경 없이 인증 헤더와 호출 주체만 바뀝니다.

### 1-1. 티켓 서명 키는 별도 발급

제안서 4절은 `AI_SERVICE_API_KEY` 재사용을 언급하고 8절은 `EXPMAP_TICKET_SECRET`
추가를 적어 서로 어긋납니다. **별도 시크릿으로 확정합니다.**

- 두 키는 회전 주기가 다릅니다
- API 키가 유출되면 티켓 위조까지 가능해집니다

### 1-2. 티켓 검증은 요청 body를 읽기 전에

파일 업로드가 프론트 → AI 직결이 되면서 최대 30MB가 AI 서버로 직접 들어옵니다.
검증 전에 버퍼링하면 **인증되지 않은 호출자가 그만큼 밀어넣을 수 있습니다.**
미들웨어가 헤더만 보고 401을 반환하도록 순서를 고정합니다.

### 1-3. 프론트 직결에 따른 AI 서버 추가 책임

제안서에 없던 항목입니다. AI 서버가 브라우저에 직접 노출되므로 아래가 필요합니다.

| 항목 | 내용 |
| --- | --- |
| CORS | `ALLOWED_ORIGINS`에 웹 오리진 추가, `Authorization` preflight 허용 |
| rate limit | 티켓 `sub`(사용자) 단위. 티켓 발급 자체의 제한은 메인 책임 |
| 요청 크기 | 파일 3개 × 10MB |

### 1-4. 재시도 시 티켓 만료 처리

티켓 TTL 5분 < 재시도 TTL 30분이라 20분 뒤 재시도하면 티켓이 만료돼 있습니다.
**재발급 후 실패한 요청의 `request_id`를 그대로 사용**합니다.

---

## 2. DB 소유권 — 수용

경험 맵 쓰기를 메인 서버 단독으로 옮기고, AI 서버는 읽기 전용 계정으로 조회만 합니다.
커밋은 `POST /api/v1/experience-map/commit` 위임입니다.

`GET /templates`, `GET /commit/{request_id}` 신설도 제안대로 수용합니다.
`section_kind`를 API 전용 식별자로 두는 것(`DETAIL`/`ACHIEVEMENT`/`TASK`/
`PROBLEM_SOLVING`/`LEARNING`)도 수용합니다.

### 2-1. 템플릿 빈 슬롯은 AI가 items에 포함해 보냅니다

기존 문서에 "템플릿을 쓰면 모든 슬롯을 생성하고 정보가 없는 블록은 비운 채 만든다"는
규칙이 있는데, 제안서는 이걸 누가 하는지 다루지 않았습니다.

**AI가 빈 슬롯도 items에 넣습니다.** `slot_id`만 지정하고 `content`는 생략합니다.

메인이 템플릿을 전개해 주는 방식보다 나은 이유:

- 계약이 단순합니다. items 외에 "이 템플릿 적용" 같은 특수 명령이 없습니다
- AI가 어떤 슬롯을 만들지 완전히 제어합니다
- 메인은 받은 items를 적용만 하면 됩니다

### 2-2. `dropped`는 AI 서버가 채웁니다

제안서 B-1 응답에 `dropped`가 있지만, B-4 오류표는 위계·소유권 위반을 422 전체
실패로 처리합니다. **항목만 탈락시키는 경로가 메인 쪽에 없습니다.**

validate 보정을 2회 초과한 항목은 **커밋 요청 items에서 아예 빠지므로** 메인 서버는
그 존재를 모릅니다. 따라서:

- **커밋 API 응답에서 `dropped` 제거**
- SSE `commit_result`의 `dropped`는 AI 서버가 자기가 제외한 항목으로 채움

### 2-3. 카탈로그 캐시 무효화 방법 추가

제안서는 "`version`이 바뀌면 갱신"이라고만 되어 있고, AI가 버전 변경을 **어떻게 아는지**가
없습니다. 기동 시 1회만 조회하면 운영 중 문구 변경이 반영되지 않습니다.

- **기동 시 1회 + 1시간 TTL 재조회**
- 커밋에서 `422 unknown_slot_id`를 받으면 **즉시 재조회 후 1회 재시도**

`unknown_slot_id`는 제안서에 없던 오류 코드입니다. 카탈로그가 갈렸을 때 감지할
수단이 이것뿐이라 추가했습니다.

### 2-4. 커밋 응답 유실 시 복구 시점

제안서 B-6이 새 실패 모드를 지적했지만 "lease 만료 후 복구 작업"까지만 적혀 있습니다.
**어느 시점에 확인하는지**를 정합니다.

`GET /state`와 `GET /requests/{request_id}`가 **만료된 lease를 정리할 때**
`GET /commit/{request_id}`를 먼저 호출합니다.

| 확인 결과 | 처리 |
| --- | --- |
| `committed: true` | 저장된 결과를 채우고 `completed` |
| `committed: false` | `retryable` `failed` |

lease가 살아 있으면 `running`을 그대로 반환하고, 프론트는 폴링을 유지합니다.
재시도 버튼은 `failed`일 때만 노출합니다.

---

## 3. 이 결정으로 메인 서버가 만들 것

| 구분 | 항목 |
| --- | --- |
| DB | `experience_map`, `ai_experience_session`, `ai_experience_request`, `ai_commit_log`, **`block.placeholder` 컬럼** |
| DB | AI 서버용 읽기 전용 계정 (`block`·`block_kind`·`experience_map` SELECT, `ai_experience_*` 쓰기) |
| API | `POST /ticket` |
| API | `POST /commit`, `GET /commit/{request_id}` |
| API | `GET /templates` |
| API | `POST /revert` |
| 로직 | 위계 권한·소유권·`is_text_editable` 검증 |
| 로직 | `level`·`position`·`kind` 계산, 형제 position 재배치 |
| 로직 | `slot_id` → placeholder 문구 부여, `section_kind` → DB enum 매핑 |
| 로직 | 템플릿 카탈로그, 신규 사용자 초기 데이터 생성 |
| 로직 | 에디터 변경 시 `map_version` 증가와 `ai_commit_log` 삭제 |

## 4. AI 서버가 만들 것

| 구분 | 항목 |
| --- | --- |
| 추가 | 티켓 검증 미들웨어, CORS·rate limit |
| 추가 | 커밋 API 클라이언트, `409`·`422` 처리, `GET /commit` 복구 조회 |
| 추가 | 템플릿 카탈로그 조회·캐시, `slot_id` 선택 로직 |
| 제거 | 커밋 트랜잭션, 위계 검증, `level`·`position` 계산, `ai_commit_log` UPSERT |
| 제거 | `block`·`experience_map` 쓰기 권한 |
| 유지 | 경험 맵 직접 조회(읽기), `ai_experience_*` 쓰기, 재구성 판단 |

## 5. 남은 항목

| # | 항목 | 결정 주체 |
| --- | --- | --- |
| 1 | 템플릿 카탈로그 본체를 코드 상수로 둘지 DB 테이블로 둘지 | 메인 서버 내부 |

응답 형태가 같아 AI 서버 구현에는 영향이 없습니다. 운영 중 문구를 자주 튜닝할
계획이면 테이블, 사실상 고정이면 코드 상수를 권합니다.

제안서에서 "해소"로 표시한 항목은 모두 확인했습니다.

| 기존 미결 | 해소 방식 |
| --- | --- |
| `SECTION_*` enum 실제 값 | API 전용 식별자로 정의, DB enum 비노출 |
| `block.placeholder` 저장 위치 | 컬럼 신설, 메인이 `slot_id`로 부여 |
| 템플릿 카탈로그 소유 주체 | 메인 단독, `GET /templates`로 제공 |
| `position` 재계산 정책 | 메인 소유, 에디터 드래그 정렬과 동일 로직 |
| 세션 소유권 검증 주체 | 티켓 `sid` == path `session_id` 검사 |
| 프록시 SSE 이벤트 화이트리스트 | 프록시 제거로 무의미 |
