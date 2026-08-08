# 경험정리 에이전트 PR 분할 계획

> **범위**: folioo-ai(AI 서버) 구현을 PR 단위로 쪼갠 실행 계획.
>
> 기준 문서: [에이전트 통합 문서](experience-map-agent.md) 9절 개발 순서,
> [API 명세](experience-map-api-spec.md)
>
> 표기: 각 PR의 "단계"는 통합 문서 9절 번호, "명세"는 5절 노드 번호입니다.

---

## 1. 분할 원칙

1. **한 PR = 한 리뷰 관심사.** 리뷰어가 한 번에 판단해야 할 질문이 두 개면 쪼갭니다.
2. **머지된 dev는 항상 초록.** 모든 PR은 자체 테스트를 포함하고 `ruff check .`·
   `ruff format --check .`·`pytest`를 통과한 상태로 머지합니다.
3. **미완성 기능을 사용자에게 노출하지 않습니다.** 노드가 다 붙기 전까지 API는
   feature flag 뒤에 둡니다 (2절).
4. **메인 서버 작업은 PR 대상이 아닙니다.** 통합 문서 9절의 1번(DB migration)과
   21번(되돌리기)은 메인 백엔드 몫이며, 여기서는 외부 의존으로만 추적합니다 (5절).
5. **기존 코드를 건드리는 변경은 맨 앞에.** EM-01은 checkpointer·DB 연결을 바꾸므로
   다른 작업이 쌓이기 전에 머지합니다.

## 2. 머지 전략

**dev 직접 머지 + feature flag**를 권장합니다. 장수 feature 브랜치를 두면 23개 PR이
쌓이는 동안 dev와 벌어져 마지막에 충돌이 몰립니다.

- 신규 코드는 대부분 `features/experience_map/` 아래라 기존 기능과 충돌하지 않습니다.
- 예외는 EM-01(공용 DB·checkpointer)과 EM-10(`app/api/v1/__init__.py` router 등록).
- `EXPERIENCE_MAP_ENABLED` 환경변수를 두고 **EM-10에서 도입, 기본값 `false`**로
  머지합니다. `true`로 뒤집는 시점은 EM-23 통과 후입니다.

브랜치 이름은 레포 관례를 따릅니다 — `feat/{issue}-{slug}`, `chore/…`, `fix/…`.

## 3. PR 목록

크기 기준: **S** 하루 이내 · **M** 2~3일 · **L** 3일 이상(더 쪼갤 여지 검토).

| # | PR | 단계 | 의존 | 크기 |
| --- | --- | --- | --- | --- |
| EM-01 | DB 연결 분리와 checkpointer 정리 | 2 | — | S |
| EM-02 | feature 스캐폴드·스키마·오류 | 3 | — | M |
| EM-03 | 템플릿 카탈로그 클라이언트와 캐시 | 3 | 02 | S |
| EM-04 | 세션·요청 Repository | 4 | 01, 02, **외부-A** | M |
| EM-05 | 경험 맵 Repository와 alias 변환 | 5 | 01, 02, **외부-A** | M |
| EM-06 | 커밋 클라이언트 | 5 | 02, 03, **외부-B** | M |
| EM-07 | 임시 첨부 파일 저장 | 7 | 02 | M |
| EM-08 | LangGraph 상태와 checkpoint | 6 | 02 | S |
| EM-09 | 티켓 검증·CORS·rate limit | 8 | 02 | M |
| EM-10 | API·SSE 뼈대 (mock graph) | 8 | 04, 07, 08, 09 | L |
| EM-11 | Router와 Fallback | 9, 18 | 08, 10 | M |
| EM-12 | 파일처리 | 10 | 07, 11 | M |
| EM-13 | 반영 내용 필터링 | 11 | 11 | M |
| EM-14 | 대상 활동 선택 | 12 | 05, 13 | S |
| EM-15 | 블록 구조화 | 13 | 03, 05, 13, 14, **외부-C** | L |
| EM-16 | 문장 정제 | 14 | 15 | M |
| EM-17 | validate와 보정 loop, graph 배선 | 15 | 15, 16 | M |
| EM-18 | 커밋 위임과 version 충돌 복구 | 16 | 06, 17 | M |
| EM-19 | gap 분석 | (누락) | 17 | M |
| EM-20 | 결과 응답과 coordinator 병렬 | 17 | 18, 19 | M |
| EM-21 | 사용자 재시도 | 19 | 20 | M |
| EM-22 | 연결 종료와 복구 | 20 | 20 | M |
| EM-23 | 시나리오 테스트와 운영 검증 | 22 | 전부 | L |

### 의존 그래프

```text
EM-01 ─┬─ EM-04 ─┐
       └─ EM-05 ─┤
                 │
EM-02 ─┬─ EM-03 ──┴─ EM-06 ────────────────────────┐
       ├─ EM-07 ─┐                                 │
       ├─ EM-08 ─┼─ EM-10 ─ EM-11 ─┬─ EM-12        │
       └─ EM-09 ─┘                 └─ EM-13 ─ EM-14│
                                          │        │
                                    EM-15 ─ EM-16 ─ EM-17 ─┬─ EM-18 ─┐
                                                           └─ EM-19 ─┴─ EM-20
                                                                          │
                                                              EM-21 ──────┤
                                                              EM-22 ──────┤
                                                              EM-23 ──────┘
```

**병렬 가능 구간** — 인원이 둘 이상이면 여기서 나눕니다.

- 1주차: EM-01 / EM-02 → 이후 EM-03·EM-07·EM-08·EM-09를 4갈래로
- EM-04·EM-05·EM-06은 서로 독립 (메인 서버 계약만 있으면 됨)
- EM-15 이후 노드 체인은 **직렬**입니다. 이 구간이 임계 경로입니다.

---

## 4. PR 상세

### EM-01 — DB 연결 분리와 checkpointer 정리

**브랜치**: `chore/{issue}-experience-map-db-split` · **단계** 2

경험 맵 DB와 LangGraph checkpoint DB를 분리합니다.

- `common/db/connection.py`의 asyncpg pool을 앱 lifespan에서 생성·종료
- `common/checkpointer/factory.py`에서 `DATABASE_URL` fallback 제거,
  `CHECKPOINT_DATABASE_URL` 미설정 시 시작 실패
- `/health`에 경험 맵 DB 연결 상태 추가, pool 크기와 statement timeout 설정

**DoD**
- [ ] 경험 맵 DB에 LangGraph checkpoint 테이블이 생성되지 않음
- [ ] 앱 종료 시 두 DB pool이 모두 닫힘
- [ ] `CHECKPOINT_DATABASE_URL` 없이 기동하면 명시적 오류로 실패

> ⚠️ **fallback 제거는 배포 환경변수를 깨뜨립니다.** 머지 전에 모든 환경(local·dev·
> prod)에 `CHECKPOINT_DATABASE_URL`이 설정돼 있는지 확인하고, 배포 순서를
> PR 설명에 적습니다. 최근 커밋 `ac2e53b`(checkpointer AsyncConnectionPool 교체)와
> 같은 파일을 건드리므로 rebase 충돌을 먼저 확인하세요.

---

### EM-02 — feature 스캐폴드·스키마·오류

**브랜치**: `feat/{issue}-experience-map-schemas` · **단계** 3

`features/experience_map/` 패키지를 만들고 **순수 모델만** 넣습니다. 이 PR에는
LLM 호출도 DB 접근도 없습니다.

- `config.py` — 환경변수 설정 모델, 노드별 timeout, LLM client 내장 retry 0
- `schemas.py` — 8-1 structured output 모델, add·update operation 모델
  (`section_kind`·`slot_id`), `active_gap` 모델
- `app/schemas/experience_map.py` — API request/response, SSE event 모델
- `errors.py` — feature exception과 HTTP/SSE 오류 매핑, 공통 API key·티켓 오류 포맷

**DoD**
- [ ] UUID·십진 문자열 ID·조건부 필수값 검증
- [ ] `content` 조건부 필수 검증 (템플릿 빈 슬롯·카테고리 컨테이너는 생략)
- [ ] `parent_ref`와 `parent_item_id` 동시 지정이 스키마에서 거부됨
- [ ] API 명세 4-2·6절 예시 JSON과 직렬화 결과가 일치

**리뷰 포인트**: 이 PR의 모델이 이후 21개 PR의 계약입니다. 명세와 필드 단위로
대조하는 것이 여기서 들이는 리뷰 비용 중 가장 값싼 부분입니다.

---

### EM-03 — 템플릿 카탈로그 클라이언트와 캐시

**브랜치**: `feat/{issue}-experience-map-template-catalog` · **단계** 3

- `templates.py` — `GET /templates` 조회, 기동 1회 + 1시간 TTL 갱신
- `unknown_slot_id` 수신 시 강제 재조회 훅 (실제 사용은 EM-18)

**DoD**
- [ ] 캐시 만료 시 재조회, 만료 전에는 재조회하지 않음
- [ ] 카탈로그 조회 실패 시 기동을 막지 않고 첫 사용 시점에 재시도
- [ ] 동시 요청이 카탈로그를 중복 조회하지 않음 (single-flight)

> 메인의 `GET /templates`가 아직 없어도 **API 명세 기준 계약 테스트로 진행**합니다.
> 카탈로그 본체 위치(코드 상수 vs DB)는 미정이지만 AI 서버 영향이 없습니다
> (통합 문서 10절 3번).

---

### EM-04 — 세션·요청 Repository

**브랜치**: `feat/{issue}-experience-map-session-repo` · **단계** 4

`repository.py`의 세션·요청 부분. `get_or_create_session`, `get_session`,
`claim_request`, `renew_request_lease`, `get_request`, `mark_request_failed`,
`mark_request_completed`, 만료 running 요청 정리, 30일 경과 완료 요청 정리.

**DoD**
- [ ] 여러 worker에서 같은 세션을 동시 실행해도 하나만 성공
- [ ] 프로세스 중단 뒤 lease 만료로 복구
- [ ] 다른 사용자 세션·요청 접근 차단
- [ ] 같은 request ID·같은 hash는 저장 상태 반환, 다른 hash는 충돌
- [ ] 30초 주기 lease 갱신 task 실패 시 실행 중단하고 failed 저장

**리뷰 포인트**: `ai_experience_request`가 API 상태의 유일한 기준입니다 (7-3).
checkpoint status를 상태 판단에 쓰는 코드가 들어오지 않았는지 봅니다.

---

### EM-05 — 경험 맵 Repository와 alias 변환

**브랜치**: `feat/{issue}-experience-map-map-repo` · **단계** 5

`repository.py`의 읽기 부분과 6-1 별칭 화이트리스트.

- `get_map(user_id)` — **읽기 전용**, flat block → 정렬된 tree
- 그룹·활동 outline, 선택 활동 full context, map version 조회
- 실제 ID ↔ alias 양방향 변환, 들여쓰기 트리 텍스트 렌더링
- 빈 블록은 `(빈 블록 — 가이드: …)`로 표시

**DoD**
- [ ] 매핑에 없는 alias는 해당 항목만 탈락시키고 나머지는 살림
- [ ] 다른 사용자·다른 활동 alias가 역변환되지 않음
- [ ] placeholder와 사용자 작성 내용이 렌더링에서 구분됨
- [ ] 같은 맵에 대해 alias 부여가 요청 안에서 안정적(deterministic)

**리뷰 포인트**: 배정 오류 방어의 1차 방어선입니다 (6절). 테스트를 두껍게 씁니다.

---

### EM-06 — 커밋 클라이언트

**브랜치**: `feat/{issue}-experience-map-commit-client` · **단계** 5

`main_client.py`. **HTTP 클라이언트 계층만** 담당하고, 충돌 시 graph 재실행 판단은
EM-18로 넘깁니다.

- `POST /commit`에 `user_id`·`request_id`·`base_map_version`·items 전달
- `409 map_version_conflict`·`422 unknown_slot_id`를 타입 있는 예외로 승격
- `422`는 카탈로그 재조회 후 1회 재시도 (클라이언트 내부에서 완결)
- `GET /commit/{request_id}` 복구 조회

**DoD**
- [ ] version 충돌을 일반 오류와 구분
- [ ] 같은 `request_id` 재호출 시 기존 commit 결과 반환
- [ ] 커밋 응답 유실 시 `GET /commit/{request_id}`로 복구

---

### EM-07 — 임시 첨부 파일 저장

**브랜치**: `feat/{issue}-experience-map-upload-store` · **단계** 7

`upload_store.py`.

- TXT·DOCX·PPTX·PDF·PNG·JPEG의 MIME·확장자·file signature 검사
  (`.txt`는 UTF-8 디코딩)
- 요청당 최대 3개, 파일당 최대 10MB, 업로드 중 SHA-256 계산
- GCS request 전용 임시 object, 추출 성공 즉시 삭제, 추출 실패 1시간 TTL
- request claim 실패 또는 저장 결과 재전송이면 방금 올린 object 즉시 삭제
- 만료 object 정리 job 또는 bucket lifecycle

**DoD**
- [ ] 다른 worker에서 추출 재시도 가능
- [ ] 추출 실패 후 1시간 안에는 원본으로 재시도
- [ ] **파일명·본문·추출 원문이 로그에 남지 않음**
- [ ] 확장자만 위조한 파일이 signature 검사에서 거부됨

---

### EM-08 — LangGraph 상태와 checkpoint

**브랜치**: `feat/{issue}-experience-map-state` · **단계** 6

`state.py`. 통합 문서 7-1의 상태를 구현합니다. `thread_id = session_id`,
`checkpoint_ns = experience_map`.

**DoD**
- [ ] 요청 시작 시 turn 전용 필드가 초기화됨
- [ ] 이전 대화는 유지되고 새 요청의 중간 필드는 섞이지 않음
- [ ] 실패 superstep을 `ainvoke(None, config)`로 이어서 실행
- [ ] checkpoint에 직렬화 불가능한 값이 들어가지 않음

---

### EM-09 — 티켓 검증·CORS·rate limit

**브랜치**: `feat/{issue}-experience-map-ticket-auth` · **단계** 8

보안 경계라 EM-10에서 떼어 별도로 리뷰합니다.

- 티켓 검증 미들웨어 (서명 → 만료 → `sid` == path `session_id` 순)
- **검증은 요청 body를 읽기 전에** 수행
- CORS에 웹 오리진과 `Authorization` preflight 추가
- 티켓 `sub` 단위 rate limit

**DoD**
- [ ] 위조·만료 티켓과 `sid` 불일치가 각각 차단되고 오류 코드가 구분됨
- [ ] body를 읽지 않고 거부되는 것이 테스트로 확인됨
- [ ] 서명 검증에 상수 시간 비교 사용

---

### EM-10 — API·SSE 뼈대 (mock graph)

**브랜치**: `feat/{issue}-experience-map-api-skeleton` · **단계** 8 · **크기 L**

```text
POST /api/v1/experience-map/sessions
GET  /api/v1/experience-map/sessions/{session_id}/state
POST /api/v1/experience-map/sessions/{session_id}/chat/stream
POST /api/v1/experience-map/sessions/{session_id}/retry/stream
GET  /api/v1/experience-map/sessions/{session_id}/requests/{request_id}
```

- `app/api/v1/__init__.py`에 router 등록, `langgraph.json`에 graph 등록
- 10초 heartbeat, stream 시작 전 JSON 오류 / 시작 후 SSE 오류
- 이벤트: `processing_started`·`node_status`·`commit_result`·`message_complete`·
  `suggestion_ready`·`processing_complete`·`error`·`ping`
- **`EXPERIENCE_MAP_ENABLED` 도입, 기본값 `false`** (2절)

**DoD**
- [ ] mock graph로 전체 API 계약 테스트 통과
- [ ] 잘못된 업로드는 stream을 열기 전에 거부
- [ ] 브라우저에서 직접 SSE 연결 확인
- [ ] flag가 `false`면 라우트가 등록되지 않음

> L 크기입니다. 리뷰가 무거우면 **엔드포인트 5개(요청/응답) → SSE 이벤트 스트림**
> 두 PR로 쪼갤 수 있습니다. 다만 mock graph 계약 테스트가 두 PR에 걸쳐 반쪽이
> 되므로, 리뷰어를 붙일 수 있으면 한 PR로 가는 편이 낫습니다.

---

### EM-11 — Router와 Fallback

**브랜치**: `feat/{issue}-experience-map-router-fallback` · **단계** 9, 18 ·
**명세** 5-1, 5-11

Router와 Fallback을 함께 넣습니다. **Fallback이 Router의 목적지 중 하나**라서
따로 머지하면 어느 쪽도 end-to-end로 돌지 않습니다. 이 PR이 mock 없이 도는
첫 경로입니다: 채팅 입력 → `out_of_scope` → fallback → `message_complete`.

- `file_input`은 코드 판정, `chat_input`/`out_of_scope`만 LLM
- `out_of_scope` 판정은 **보수적으로** — 여지가 있으면 `content_filter`
- gap 답변 여부는 Router가 판정하지 않음
- fallback_reason 4종과 경로별 고정 문구 (5-11)

**DoD**
- [ ] 진입 경로 4가지가 각각 자기 문구를 내보냄 (문구 하나로 합쳐지지 않음)
- [ ] 어느 경로든 DB 변경 없이 completed로 저장되고 재시도 버튼이 노출되지 않음
- [ ] LLM 분류가 재시도 후에도 실패하면 fallback
- [ ] `active_gap`이 있어도 Router가 gap 답변으로 분기하지 않음

---

### EM-12 — 파일처리

**브랜치**: `feat/{issue}-experience-map-file-processor` · **단계** 10 · **명세** 5-2

- 파서(TXT·DOCX·PPTX)와 OCR(PDF·PNG·JPG) 분기, 섞이면 **입력 순서대로** 이어 붙임
- 파일별 추출 결과와 source hash 저장, 파일별·전체 context 길이 제한
- 추출 결과를 checkpoint에 저장한 뒤 GCS 원본 즉시 삭제

**DoD**
- [ ] **품질 문제로 추출 불가 → `fallback`, 시스템 오류 → 노드 실패**로 구분됨
- [ ] 추출 완료 뒤에는 원본 파일 없이 재시도
- [ ] 추출 노드 실패 시에는 GCS 원본으로 재시도
- [ ] 파서 형식과 OCR 형식을 섞어 업로드해도 순서 유지

**리뷰 포인트**: 두 실패의 구분이 이 PR의 핵심입니다. 손상된 PDF에 재시도 버튼을
주면 몇 번을 눌러도 같은 결과입니다.

---

### EM-13 — 반영 내용 필터링

**브랜치**: `feat/{issue}-experience-map-content-filter` · **단계** 11 · **명세** 5-3

입력을 gap 답변 / 새로 반영할 내용 / 반영 제외로 분류하고 후속 노드를 결정합니다.
경험정리 내용 조회 tool은 **조건부 호출**입니다.

**DoD**
- [ ] structured output schema 검증
- [ ] 모든 출력 item을 원문 source로 역추적 가능
- [ ] `active_gap`이 없을 때 gap 답변으로 분류되지 않음
- [ ] 후속 노드 분기 5가지가 명세 표대로 동작
- [ ] 입력에 없는 역할·성과·수치가 생성되지 않음

---

### EM-14 — 대상 활동 선택

**브랜치**: `feat/{issue}-experience-map-target-activity` · **단계** 12 · **명세** 5-4

작지만 fallback(`ambiguous_target`)과 alias 소유권이 걸려 독립시킵니다.

**DoD**
- [ ] `context_experience_id` 우선, 없으면 메시지+outline, gap 답변은
      `anchor_block_id`가 속한 활동
- [ ] **하나로 특정할 수 없으면 commit 없이 fallback**
- [ ] 한 요청이 두 활동을 수정하지 않음
- [ ] 대상이 불명확한 상태에서 DB 변경 없음

---

### EM-15 — 블록 구조화

**브랜치**: `feat/{issue}-experience-map-structure` · **단계** 13 · **명세** 5-5 ·
**크기 L**

노드 중 가장 큽니다. 텍스트를 **수정하지 않고** 위계에 맞게 분류하고, 템플릿과
`slot_id`를 고릅니다.

**DoD**
- [ ] structure 전후 item 집합 동일
- [ ] 1·2단계 생성과 편집 불가 block 수정 차단
- [ ] 이미 있는 카테고리를 중복 생성하지 않음
- [ ] 입력 텍스트가 그대로 유지됨 (구체성 손실 없음)
- [ ] 생성한 블록마다 올바른 `slot_id`가 지정됨
- [ ] 문제해결 5단계 템플릿 6종 선택이 내용과 일치
- [ ] **템플릿 사용 시 정보가 없는 슬롯도 빈 블록으로 생성됨** (3-1)
- [ ] 템플릿 미사용 시 빈 블록이 생성되지 않음

> 🚧 **`slot_id` 38개 목록이 확정돼야 완결됩니다** (외부-C). 완화책: `slot_id`를
> 코드 상수로 박지 말고 **EM-03 카탈로그에서 받아온 목록으로만** 검증하도록 짜고,
> 테스트는 fixture 카탈로그로 돌립니다. 그러면 목록 확정이 늦어져도 이 PR을
> 머지할 수 있고, 확정 후에는 fixture만 교체됩니다.

---

### EM-16 — 문장 정제

**브랜치**: `feat/{issue}-experience-map-refine` · **단계** 14 · **명세** 5-6

**DoD**
- [ ] 정제 전후 operation metadata 동일 (출력 스키마에 배정 필드 없음)
- [ ] 입력 item 집합 == 출력 item 집합 검증
- [ ] 원문 근거가 없는 수치·고유명사 생성 차단
- [ ] gap `extend_block` 결합 시 기존 내용이 유실되지 않음
- [ ] 명사 종결과 구조적 표기(`→`, `/`) 적용
- [ ] 한 활동 단위로 1회 호출 (블록별 분할 호출 아님)

---

### EM-17 — validate와 보정 loop, graph 배선

**브랜치**: `feat/{issue}-experience-map-validate-graph` · **단계** 15 · **명세** 5-7

`validate.py`와 `graph.py`. 여기서 그래프가 처음으로 전부 연결됩니다.

- 검증 8항목 (5-7), 회귀 분기: 위계·권한 위반 → structure / 글자수 위반 → refine
- 보정 **최대 2회**, 초과 항목은 커밋 items에서 제외하고 `dropped`에 담음
- PostgreSQL checkpointer로 compile, 자동 재시도 대상에 `RetryPolicy(max_attempts=2)`
- **gap 분석·제안·커밋에는 RetryPolicy 미적용**
- Fallback과 validate 성공 경로는 graph를 종료하고 coordinator로 넘김

**DoD**
- [ ] 모든 graph 분기가 테스트로 커버됨
- [ ] 보정 loop가 2회에서 멈추고 무한 루프하지 않음
- [ ] 항목 하나가 탈락해도 나머지 정상 블록이 살아남음
- [ ] 위계별 AI 권한 (1·2단계 생성 차단, 3단계 수정 차단, 전 위계 삭제 차단)

---

### EM-18 — 커밋 위임과 version 충돌 복구

**브랜치**: `feat/{issue}-experience-map-commit` · **단계** 16 · **명세** 5-8

EM-06 클라이언트 위에 **재실행 판단**을 얹습니다.

- alias → 실제 ID 역변환 후 items 전송, commit 구간 cancellation shield
- `409` → 최신 맵 재조회 → 구조 유지 시 validate부터, 구조 변경 시 structure부터
  한 번 재실행. **두 번째 충돌은 `commit_conflict`**
- 응답을 `ai_experience_request.result`에 저장

**DoD**
- [ ] SSE가 끊겨도 commit 중복 실행 없음 (메인이 `request_id` 기준 멱등)
- [ ] 커밋 성공 후 응답이 유실돼도 `GET /commit/{request_id}`로 복구
- [ ] `409` 1회 복구와 2회째 최종 실패가 구분됨

---

### EM-19 — gap 분석

**브랜치**: `feat/{issue}-experience-map-gap-analysis` · **명세** 5-10

- 입력은 **이번 턴에 커밋될 items** (validate 통과 시점에 확정)
- 우선순위 5단계에서 **최대 1개**, gap 유형 `extend_block`/`new_child_block`
- gap이 없으면 고정 문구, `active_gap`에 저장하고 없으면 `null`

**DoD**
- [ ] **방금 커밋한 내용을 누락으로 지적하지 않음**
- [ ] gap 없음과 gap 분석 실패가 구분됨 (실패일 때만 이벤트 생략)
- [ ] 한 응답에 gap이 2개 이상 나오지 않음
- [ ] 생성한 gap이 다음 턴 `active_gap`으로 이어짐

> ℹ️ 통합 문서 9절 개발 순서에 **gap 분석 노드의 독립 항목이 없습니다.** 17번
> coordinator에 묻혀 있는데, 프롬프트가 있는 LLM 노드라 별도 PR로 뺐습니다.

---

### EM-20 — 결과 응답과 coordinator 병렬

**브랜치**: `feat/{issue}-experience-map-coordinator` · **단계** 17 ·
**명세** 5-9, 9절 17

`coordinator.py`와 `result_response.py`.

- 결과 응답은 **LLM을 쓰지 않는 결정적 템플릿**, 경로(`{experience_name} >
  {category_label}`) 필수
- commit task와 gap task를 동시 시작 → commit await → gap await
- commit 실패면 gap task 취소, gap 실패면 이벤트만 생략

**DoD**
- [ ] 느린 gap 분석이 결과 응답 전송을 지연하지 않음
- [ ] gap 실패가 완료 요청을 failed로 바꾸지 않음
- [ ] 커밋 실패 뒤 suggestion 이벤트가 전송되지 않음
- [ ] 두 task가 서로 다른 state 필드에만 씀
- [ ] `dropped_count`가 있으면 안내 문구가 덧붙음

> 결과 문구는 **초안**입니다 (통합 문서 10절 2번). 변수(`{experience_name}`,
> `{category_label}`, `{added_count}`, `{updated_count}`, `{dropped_count}`)를
> 분리해 두면 기획 확정 시 문구만 교체됩니다 — 이 PR에서 그 분리를 지킵니다.

---

### EM-21 — 사용자 재시도

**브랜치**: `feat/{issue}-experience-map-user-retry` · **단계** 19

- 마지막 요청·failed 상태·retryable·30분 TTL 확인
- 텍스트 추출 미완료면 GCS object TTL 추가 확인, request lease 재획득
- 최신 경험 맵과 version 재조회 후 실패 superstep부터 resume
- commit 결과가 이미 있으면 commit을 건너뛰고 completed로 복구

**DoD**
- [ ] 성공한 이전 노드는 다시 실행하지 않음
- [ ] 새 요청 시작 뒤 이전 실패 요청 재시도 거부
- [ ] 만료 상태는 `410 retry_expired`

---

### EM-22 — 연결 종료와 복구

**브랜치**: `feat/{issue}-experience-map-disconnect-recovery` · **단계** 20

- 커밋 전 종료 → 실행 취소 후 failed, 커밋 후 종료 → suggestion 생략하고 completed
- **만료 lease 정리 시 `GET /commit/{request_id}`를 먼저 확인**
  (`committed: true` → completed / `false` → retryable failed)
- lease가 살아 있으면 `running` 유지

**DoD**
- [ ] 파일처리·LLM·커밋 각 시점에서 연결을 끊는 테스트
- [ ] 재접속 뒤 중복 block 없이 같은 결과 조회
- [ ] 커밋 성공 + 응답 유실 상태가 lease 만료 후 completed로 정리됨

---

### EM-23 — 시나리오 테스트와 운영 검증

**브랜치**: `test/{issue}-experience-map-scenarios` · **단계** 22 · **크기 L**

통합 문서 22절의 시나리오 14개를 채우고 `EXPERIENCE_MAP_ENABLED`를 뒤집습니다.

**DoD**
- [ ] 시나리오 14개 전부 통과 (파일 파서/OCR, 채팅, gap 답변 2분기, 동시 입력,
      카테고리 생성, 템플릿 2종, fallback 2종, 재시도, gap 실패, SSE 단절)
- [ ] DB·연동 통합 테스트 7개 통과
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 전부 통과
- [ ] `EXPERIENCE_MAP_ENABLED` 기본값을 `true`로 전환

---

## 5. 외부 의존

AI 서버 PR이 아니지만 이 계획을 막을 수 있는 항목입니다.

| ID | 항목 | 주체 | 막는 PR | 완화책 |
| --- | --- | --- | --- | --- |
| 외부-A | 메인 DB migration (통합 문서 9절 1번) | 메인 서버 | EM-04, EM-05 | 로컬 테스트 DB에 동일 스키마 픽스처를 두고 선행 개발 |
| 외부-B | `POST /commit`·`GET /commit/{id}` | 메인 서버 | EM-06, EM-18 | API 명세 기준 mock 서버로 계약 테스트 |
| 외부-C | **`slot_id` 38개 목록** | 기획·메인 서버 | EM-15 | 카탈로그 주입식으로 구현 + fixture 테스트 (EM-15 참고) |
| 외부-D | 결과 응답 문구 확정 | 기획 | — (막지 않음) | 변수 분리로 문구만 교체 |
| 외부-E | `GET /templates` | 메인 서버 | EM-03 | 계약 테스트로 선행 |
| 외부-F | 되돌리기 API (9절 21번) | 메인 서버 | — (AI 작업 없음) | — |

**외부-A와 외부-C가 임계 경로입니다.** 특히 외부-C는 EM-15가 직렬 체인의 한가운데라
지연이 그대로 전체 일정으로 넘어갑니다. 착수와 동시에 기획에 요청하세요.

## 6. 통합 문서 9절 ↔ PR 대응

| 9절 단계 | PR |
| --- | --- |
| 1 메인 DB migration | 외부-A |
| 2 DB 연결 분리 | EM-01 |
| 3 설정·스키마·오류 | EM-02, EM-03 |
| 4 세션·요청 Repository | EM-04 |
| 5 경험 맵 Repository와 커밋 클라이언트 | EM-05, EM-06 |
| 6 LangGraph 상태와 checkpoint | EM-08 |
| 7 임시 첨부 파일 저장 | EM-07 |
| 8 API·SSE 뼈대 | EM-09, EM-10 |
| 9 Router | EM-11 |
| 10 파일처리 | EM-12 |
| 11 반영 내용 필터링 | EM-13 |
| 12 대상 활동 선택 | EM-14 |
| 13 블록 구조화 | EM-15 |
| 14 문장 정제 | EM-16 |
| 15 validate와 보정 loop | EM-17 |
| 16 커밋 위임과 version 충돌 복구 | EM-18 |
| 17 결과·gap 병렬 coordinator | EM-19, EM-20 |
| 18 Fallback | EM-11 |
| 19 사용자 재시도 | EM-21 |
| 20 연결 종료와 복구 | EM-22 |
| 21 되돌리기 연동 | 외부-F |
| 22 테스트와 운영 검증 | EM-23 |
| (5-10 gap 분석 — 단계 누락) | EM-19 |
