# 경험정리 — 메인 서버에 요청할 것

> **대상**: 메인 백엔드 개발자
> **작성**: AI 서버 (folioo-ai)
> 계약: [경험정리 API 명세](experience-map-api-spec.md)
> 연결 절차: [메인 서버 연결 체크리스트](experience-map-main-server-checklist.md)

**API 명세에 적혀 있지 않거나, 적힌 것과 달라져야 하는 항목만** 모았습니다.
명세대로 구현하면 되는 것은 여기 없습니다.

각 항목에 **왜 필요한지**와 **없으면 무엇이 막히는지**를 적었습니다.

---

## 요약

### 메인 서버가 만들어 줘야 하는 것

| # | 요청 | 급함 | 막고 있는 것 |
| --- | --- | --- | --- |
| 1 | `block` · `block_kind` 테이블 DDL | **높음** | 경험 맵 읽기 → 대상 활동 선택 → 블록 구조화 |
| 2 | `ai_experience_request.owner_token` 컬럼 추가 | **높음** | 동시성 버그 수정이 운영에서 동작하지 않음 |
| 3 | `GET /templates` 응답 구조 확정 | **높음** | 카테고리 슬롯 10개를 전달할 방법이 없음 |
| 4 | AI → 메인 호출용 API 키 **값** 합의 | 중간 | 운영에서 커밋·템플릿 호출 전부 실패 |

1·2는 **메인 DB migration 이 아직 작성되지 않은 지금이 가장 싼 시점**입니다.
migration 이 나간 뒤에는 `ALTER TABLE` 을 따로 협의해야 합니다.

### 명세에 반영이 필요한 것 (구현은 이미 됨)

| # | 항목 | 되돌리면 |
| --- | --- | --- |
| 5-1 | fallback 문구 4종 분리 | 파일 손상 사용자에게 엉뚱한 안내 |
| 5-2 | `429 rate_limited` + `Retry-After` | 티켓 하나로 무제한 호출 |
| 5-3 | `EXPERIENCE_MAP_ENABLED` · `EXPMAP_RATE_LIMIT_PER_MINUTE` | 배포 시 기능이 404 |
| 5-4 | `lease_lost` 오류 코드 | — (오류표만 누락) |

**되돌릴지 명세에 넣을지 정해 주세요.** 지금은 코드를 그대로 두고 있어 문서와
구현이 다릅니다.

---

## 1. `block` · `block_kind` 테이블 DDL — 높음

### 무엇이 필요한가

두 테이블의 **`CREATE TABLE` 정의**입니다. migration 전체가 아니라 스키마만
있으면 됩니다.

### 왜 명세에 없나

API 명세 4-1 에 **조회 쿼리**가, 3-5 에 **제약**이 있습니다. 하지만 테이블
정의가 없습니다.

```sql
-- 명세 4-1 이 알려주는 것: 이런 컬럼을 읽는다
SELECT b.id, b.parent_id, b.level, b.kind, b.position, b.content,
       COALESCE(b.placeholder, k.placeholder) AS placeholder,
       k.is_text_editable, k.is_deletable
  FROM block b JOIN block_kind k ON k.kind = b.kind
 WHERE b.user_id = $1;
```

타입·기본값·인덱스·`block_kind` 의 행 구성은 알 수 없습니다.

### 없으면 무엇이 막히나

```text
3.05 경험 맵 Repository (맵 읽기·별칭 변환)
  └→ 3.14 대상 활동 선택
  └→ 3.15 블록 구조화   ← 이 기능의 핵심
        └→ 3.16 문장 정제 → 3.17 graph 배선
```

**남은 AI 노드 대부분이 여기서 막힙니다.** 추측해서 만들면 실제와 어긋나 나중에
다시 짜야 하므로, 로컬 스키마(`scripts/experience_map/schema.sql`)에도 넣지
않았습니다.

### 특히 확인하고 싶은 것

- `block.kind` 의 enum 값 목록 (`CONTENT`, `SECTION_*` 의 실제 이름)
- `block_kind` 테이블의 행 구성 — level 고정 여부, `placeholder` 기본값
- `block.placeholder` 컬럼 (명세 3-7 이 요구한 신규 컬럼)이 반영됐는지
- `user_id` 의 실제 타입 (명세는 `bigint` 로 가정)

---

## 2. `ai_experience_request.owner_token` 컬럼 — 높음

### 무엇이 필요한가

```sql
ALTER TABLE ai_experience_request ADD COLUMN owner_token uuid;
```

인덱스는 불필요합니다. 항상 PK(`user_id`, `request_id`)와 함께 조회합니다.

### 왜 명세에 없나

명세 3-3 을 쓸 때 예상하지 못한 동시성 문제 때문입니다. 코드리뷰에서 발견해
로컬 PostgreSQL 로 재현했습니다.

```text
worker A: 실행 중 (lease 갱신 실패, 프로세스는 살아 있음)
   ↓ 5분 경과
정리:     A 의 요청을 failed 로 전환
worker B: 재시도로 같은 요청을 running 으로 가져감
worker A: 뒤늦게 완료 처리 → B 의 결과를 A 의 결과로 덮어씀
```

**`status = 'running'` 조건만으로는 못 막습니다.** 행이 `running` 인 것과
내가 그 `running` 의 주인인 것은 다릅니다. 실행권을 표시할 값이 행에 있어야
합니다.

### 어떻게 쓰나

AI 서버만 읽고 씁니다. **메인 서버는 이 컬럼을 다루지 않습니다.**

- 요청을 잡거나 재시도할 때마다 AI 서버가 `uuid4()` 를 넣습니다
- lease 갱신·완료·실패는 `AND owner_token = $token` 이 맞을 때만 반영됩니다
- 만료 정리는 `owner_token` 을 `NULL` 로 비웁니다

`gen_random_uuid()` 를 쓰지 않으므로 PostgreSQL 버전이나 `pgcrypto` 확장에
의존하지 않습니다.

### 없으면 무엇이 막히나

기능은 동작합니다. 다만 **위 순서가 벌어지면 사용자 결과가 조용히 덮어써집니다.**
로그에도 남지 않습니다. 드물지만 재현되는 것을 확인했습니다.

컬럼 없이 배포하면 `UndefinedColumnError` 로 모든 요청이 실패합니다. 그래서
**컬럼 추가와 AI 서버 배포 순서가 중요합니다** — 컬럼이 먼저입니다.

---

## 3. `GET /templates` 응답 구조 — 중간

### 무엇이 필요한가

명세 7절의 응답 예시가 **같은 문서 9절이 정의한 카탈로그를 담을 수 없습니다.**
담을 수 있는 형태로 정하고 알려주세요.

### 무엇이 문제인가

**명세 안에서 두 절이 어긋나 있습니다.** 9절이 그 사실을 직접 적고 있습니다.

> 점 개수가 곧 level. **스펙 3-7이 3-part만 언급하는데 정정 필요해.**

| 항목 | 7절 응답 예시 | 9절 카탈로그 정의 |
| --- | --- | --- |
| 카테고리 슬롯 10개 | **담을 자리 없음** | `{SECTION}.{SLOT}` 로 존재 |
| 템플릿 안 slot level | 4·5 혼재 | 점 개수가 곧 level |
| `is_anchor` | 필드 없음 | `TASK.SUMMARY`·`PROBLEM_SOLVING.SUMMARY` 가 앵커 |

7절 예시는 모든 슬롯이 `templates[].slots` 아래에 있습니다.

```jsonc
{ "sections": [ { "section_id": "...", "templates": [ { "slots": [...] } ] } ] }
```

그런데 9절이 확정한 **level 4 슬롯 10개는 어떤 템플릿에도 속하지 않습니다.**

```text
카테고리 슬롯 (level 4)   10개   ← DETAIL.MOTIVATION 등. 템플릿 없음
하위 템플릿 (level 5)     28개   ← 담당업무 1종×4 + 문제해결 6종×4
                          38개
```

`DETAIL`·`ACHIEVEMENT`·`LEARNING` 은 하위 템플릿이 아예 없습니다.

### AI 서버는 일단 둘 다 받게 해 두었습니다

7절 예시를 그대로 넣으면 파싱 단계에서 거부되던 것을 고쳤습니다. 지금은 7절
형태와 아래 제안 형태를 **모두** 받고, `is_anchor` 가 없으면 `slot_id` 로
채웁니다.

**다만 이걸로 문제가 해결되지는 않습니다.** 7절 형태로 응답하는 한 카테고리 슬롯
10개는 전달될 방법이 없고, 그러면 AI 가 카테고리 슬롯을 만들 수 없습니다.

### 제안하는 형태

```jsonc
{
  "version": "2026-08-09",
  "sections": [
    {
      "section_id": "DETAIL",
      "label": "상세정보",
      "slots": [                     // level 4. 템플릿에 속하지 않는다
        { "slot_id": "DETAIL.MOTIVATION", "level": 4,
          "placeholder": "...", "example": "..." }
      ],
      "templates": []                // 하위 템플릿 없음
    },
    {
      "section_id": "PROBLEM_SOLVING",
      "label": "문제해결",
      "slots": [
        { "slot_id": "PROBLEM_SOLVING.SUMMARY", "level": 4,
          "is_anchor": true,         // 이 아래에만 level 5 가 붙는다
          "placeholder": "...", "example": "..." }
      ],
      "templates": [
        { "template_id": "TROUBLESHOOTING", "label": "기술 트러블슈팅",
          "slots": [ { "slot_id": "PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE",
                       "level": 5, "placeholder": "...", "example": "..." } ] }
      ]
    }
  ]
}
```

**형태는 메인 서버가 정하시면 됩니다.** 38개를 표현할 수만 있으면 AI 서버가
맞추겠습니다.

### 함께 정정이 필요한 곳

`slot_id` 형식이 level 에 따라 둘이라는 것은 **9절에 이미 적혀 있습니다.**

```text
level 4 : {SECTION}.{SLOT}              DETAIL.MOTIVATION
level 5 : {SECTION}.{TEMPLATE}.{SLOT}   PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE
```

그런데 아래 두 곳이 아직 3-part 만 전제하고 있습니다.

| 위치 | 현재 | 문제 |
| --- | --- | --- |
| 3-7 | `{SECTION}.{TEMPLATE}.{SLOT}` 만 설명 | 2-part 형식이 없음 |
| 4-2 예시 | 앵커를 `PROBLEM_SOLVING.TROUBLESHOOTING.SUMMARY` 로 표기 | 9절 앵커는 `PROBLEM_SOLVING.SUMMARY` |
| 7절 예시 | 3-part 슬롯에 `"level": 4` | 점 개수 = level 규칙과 충돌 |

---

## 4. AI → 메인 호출에 쓸 API 키 값 합의 — 중간

### 무엇이 필요한가

메인 서버가 **AI 서버에서 오는 호출**(`POST /commit`, `GET /commit/{request_id}`,
`GET /templates`)의 `X-API-Key` 를 검증할 값입니다.

### 무엇이 문제인가

명세 2-1 이 **양방향 모두 `AI_SERVICE_API_KEY`** 를 쓰도록 적고 있는데, AI 서버
구현은 아웃바운드에 `MAIN_BACKEND_API_KEY` 를 씁니다.

**명세대로 키 하나만 배포하면 커밋·템플릿 호출이 전부 이렇게 실패합니다.**

```text
RuntimeError: MAIN_BACKEND_API_KEY 환경변수가 설정되지 않았습니다.
```

### AI 서버는 방향을 나눠 두었습니다

방향마다 다른 키를 쓰는 쪽이 맞다고 판단했습니다.

| 방향 | AI 서버 변수 | 비고 |
| --- | --- | --- |
| 메인 → AI | `AI_SERVICE_API_KEY` | 기존 그대로 |
| AI → 메인 | `MAIN_BACKEND_API_KEY` | 기존 그대로 |

이유는 둘입니다.

1. **`MAIN_BACKEND_API_KEY` 는 경험정리 전용이 아닙니다.** 첨삭·포트폴리오 등
   기존 기능의 공용 클라이언트(`common/http_client`)와 이미 배포된 pptx worker
   (Secret Manager 연결 완료)가 같은 변수를 씁니다. 경험정리 명세에 맞추려고
   바꾸면 무관한 기능들이 함께 깨집니다.
2. **한 키로 묶으면 유출 영향 범위가 넓어집니다.** 인바운드 키가 새면 아웃바운드
   호출 권한까지 넘어갑니다. 티켓 서명 키를 API 키와 분리한 것과 같은 이유입니다.

### 메인 서버에 필요한 것

**값 합의뿐입니다.** 변수 이름은 각 서버 사정이고, 실제로 오가는 것은 헤더 값입니다.

- 메인 서버는 **키 두 개를 따로 보관**해야 합니다. 지금까지 명세에는 발신용
  (`AI_SERVICE_API_KEY`) 하나만 있었습니다.
- AI → 메인 호출을 검증할 값은 AI 쪽 `MAIN_BACKEND_API_KEY` 와 같아야 합니다.
  이 값은 이미 운영에서 쓰이고 있으므로, 경험정리를 위해 새로 발급할 필요는
  없습니다.

---

## 5. 명세와 구현이 어긋난 지점 — 결정 필요

**아래는 이미 구현돼 동작 중인데 명세에 없는 것들입니다.** 되돌릴지, 명세에 넣을지
정해 주세요. 지금은 코드를 그대로 두고 있어 문서와 구현이 다릅니다.

| # | 항목 | 명세 | 구현 | 되돌리면 |
| --- | --- | --- | --- | --- |
| 5-1 | fallback 문구 | 1종 | **4종 분리** | 파일 손상 사용자에게 엉뚱한 안내 |
| 5-2 | `429 rate_limited` | 없음 | 구현됨 | 티켓 하나로 무제한 호출 |
| 5-3 | `EXPERIENCE_MAP_ENABLED` | 없음 | 구현됨 | 배포 시 기능이 404 |
| 5-4 | `lease_lost` | 없음 | 구현됨 | — (오류표만 누락) |

### 5-1. fallback 문구는 4종으로 나눠져 있습니다

명세 6절 `message_complete` 는 고정 문구 하나를 규정합니다.

> Fallback은 `committed: false`이며 고정 문구 "아직 지원하지 않는 기능이에요."를 보냅니다.

구현은 진입 경로별로 다릅니다.

| `fallback_reason` | 문구 |
| --- | --- |
| `out_of_scope` | 아직 지원하지 않는 기능이에요. |
| `file_unreadable` | 파일에서 내용을 읽지 못했어요. 다른 파일로 올려 주시거나 내용을 직접 입력해 주세요. |
| `nothing_to_apply` | 정리에 반영할 내용을 찾지 못했어요. 어떤 경험을 하셨는지 알려주세요. |
| `ambiguous_target` | 어떤 경험에 정리할지 알려주세요. |

**나눈 이유가 있습니다.** 파일이 손상돼 실패한 사용자에게 "아직 지원하지 않는
기능이에요" 라고 하면 사용자는 무엇을 해야 할지 알 수 없습니다. 되돌리면 그
문제가 다시 생깁니다.

### 5-2. `429 rate_limited` 가 오류표에 없습니다

프론트가 AI 서버에 직접 붙으므로(2-1) 남용 방어가 AI 서버 몫입니다. 티켓
`sub` 단위로 분당 요청 수를 제한하고, 초과 시 `Retry-After` 헤더와 함께
`429 rate_limited` 를 보냅니다.

명세 2-1 은 "**rate limit**: 티켓 `sub`(사용자) 단위" 라고 책임만 적고, 2-3
오류표에는 코드가 없습니다. 한도는 `EXPMAP_RATE_LIMIT_PER_MINUTE`(기본 20)
입니다.

### 5-3. 운영 환경변수 두 개가 8절에 없습니다

| 변수 | 기본값 | 없으면 |
| --- | --- | --- |
| `EXPERIENCE_MAP_ENABLED` | `false` | **라우트 자체가 등록되지 않아 전부 404** |
| `EXPMAP_RATE_LIMIT_PER_MINUTE` | `20` | 기본값으로 동작 |

**`EXPERIENCE_MAP_ENABLED` 를 특히 주의해 주세요.** 명세 8절만 보고 배포하면
경험정리 API 가 통째로 404 로 보입니다. 시나리오 검증 전까지 노출하지 않으려고
둔 flag 입니다.

### 5-4. `lease_lost` 가 오류표에 없습니다

실행 중 lease 를 잃으면(다른 worker 가 요청을 가져감) 스트림을 끊고 이 코드를
보냅니다. 6절 오류 이벤트 표에 추가가 필요합니다.

---

## 참고 — 이미 명세에 반영된 것

| 항목 | 위치 |
| --- | --- |
| `slot_id` 형식 2종과 앵커 규칙 | 9절 (3-7·4-2·7절은 정정 필요, 위 3번) |
| 빈 슬롯도 items 에 포함 | 3-8, 9절 |
| 템플릿 카탈로그 본체 저장 방식 | 9절 — 메인 서버 내부 결정 |

`slot_id` 카탈로그 38개는 9절에 확정돼 있고 AI 서버 구현도 그 기준을 따릅니다.

---

## 배포 순서

메인 서버 migration 과 AI 서버 배포 사이에 순서가 있는 항목입니다.

| # | 먼저 | 그다음 |
| --- | --- | --- |
| 2 | `owner_token` 컬럼 추가 | AI 서버 배포 |
| — | `CHECKPOINT_DATABASE_URL` 환경변수 설정 | AI 서버 배포 |

두 번째는 이미 dev 에 머지된 변경입니다. `DATABASE_URL` fallback 을 제거해서,
환경변수가 없으면 앱이 기동하지 않습니다.
