# 경험정리 — 메인 서버에 요청할 것

> **대상**: 메인 백엔드 개발자
> **작성**: AI 서버 (folioo-ai)
> 계약: [경험정리 API 명세](experience-map-api-spec.md)

**API 명세에 적혀 있지 않거나, 적힌 것과 달라져야 하는 항목만** 모았습니다.
명세대로 구현하면 되는 것은 여기 없습니다.

각 항목에 **왜 필요한지**와 **없으면 무엇이 막히는지**를 적었습니다.

---

## 요약

| # | 요청 | 급함 | 막고 있는 것 |
| --- | --- | --- | --- |
| 1 | `block` · `block_kind` 테이블 DDL | **높음** | 경험 맵 읽기 → 대상 활동 선택 → 블록 구조화 |
| 2 | `ai_experience_request.owner_token` 컬럼 추가 | **높음** | 동시성 버그 수정이 운영에서 동작하지 않음 |
| 3 | `GET /templates` 응답 구조 확정 | 중간 | 템플릿 카탈로그 클라이언트 |

1·2는 **메인 DB migration 이 아직 작성되지 않은 지금이 가장 싼 시점**입니다.
migration 이 나간 뒤에는 `ALTER TABLE` 을 따로 협의해야 합니다.

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

명세 7절의 응답 예시가 **확정된 카탈로그를 담을 수 없습니다.** 담을 수 있는
형태로 정하고 알려주세요.

### 무엇이 문제인가

기존 예시는 모든 슬롯이 `templates[].slots` 아래에 있습니다.

```jsonc
{ "sections": [ { "section_id": "...", "templates": [ { "slots": [...] } ] } ] }
```

그런데 확정된 카탈로그의 **level 4 슬롯 10개는 어떤 템플릿에도 속하지
않습니다.**

```text
카테고리 슬롯 (level 4)   10개   ← DETAIL.MOTIVATION 등. 템플릿 없음
하위 템플릿 (level 5)     28개   ← 담당업무 1종×4 + 문제해결 6종×4
                          38개
```

`DETAIL`·`ACHIEVEMENT`·`LEARNING` 은 하위 템플릿이 아예 없습니다.

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

### 참고 — `slot_id` 형식은 level 에 따라 둘입니다

```text
level 4 : {SECTION}.{SLOT}              DETAIL.MOTIVATION
level 5 : {SECTION}.{TEMPLATE}.{SLOT}   PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE
```

명세 3-7·4-2 가 3-part 만 언급하던 것을 정정해 두었습니다. 점 개수가 곧
level 이라 파싱만으로 판정할 수 있습니다.

---

## 참고 — AI 서버가 명세에 추가한 것

메인 서버 작업은 아니지만, 계약 문서가 바뀌었으니 알려 드립니다.

| 항목 | 위치 | 내용 |
| --- | --- | --- |
| `429 rate_limited` | 명세 2-3 | 티켓 `sub` 단위 rate limit 초과. `Retry-After` 헤더 포함 |
| `lease_lost` | 명세 6절 (미반영) | 실행권 상실로 스트림이 끊긴 경우. 아직 오류표에 없음 |
| `EXPMAP_RATE_LIMIT_PER_MINUTE` | 명세 8절 | 기본값 20 |
| `EXPERIENCE_MAP_ENABLED` | 명세 8절 | 기능 노출 flag. 기본값 `false` |

`slot_id` 카탈로그 38개는 명세와 통합 문서에 반영을 마쳤습니다.

---

## 배포 순서

메인 서버 migration 과 AI 서버 배포 사이에 순서가 있는 항목입니다.

| # | 먼저 | 그다음 |
| --- | --- | --- |
| 2 | `owner_token` 컬럼 추가 | AI 서버 배포 |
| — | `CHECKPOINT_DATABASE_URL` 환경변수 설정 | AI 서버 배포 |

두 번째는 이미 dev 에 머지된 변경입니다. `DATABASE_URL` fallback 을 제거해서,
환경변수가 없으면 앱이 기동하지 않습니다.
