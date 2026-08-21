# PDF 텍스트 추출 활동 단위 스트리밍 계약 (제안)

> **상태**: 제안 — 메인 백엔드 리뷰 승인 전입니다. 이 문서가 승인돼야 CR-05(구현)를 시작합니다.
>
> **근거**: 화면설계서 v.3.5 [포트폴리오 첨삭 - 포트폴리오 업로드] 주석 1
> "1개의 활동 하의 포트폴리오 텍스트 추출이 완료되면, 텍스트 추출 스트리밍(1-4)을 표시하며,
> **활동 단위로 스트리밍** 한다."
>
> 관련: [첨삭 v.3.5 PR 분할 계획](correction-v35-pr-plan.md) CR-04·CR-05,
> [SSE 스트리밍 프로토콜](sse-streaming.md)

---

## 1. 현재 구조와 문제

```text
프론트 ──POST /api/v1/corrections/{id}/pdf-extraction (파일)──→ AI
                                                                 │ (background task)
                                                                 │ PDF 전체를 LLM 1회 호출
                                                                 ↓
메인 ←──POST /internal/corrections/{id}/pdf-extraction-result────┘
        {"status":"completed","activities":[...4개 전부...]}
```

추출이 **전부 끝난 뒤에 한 번** 콜백이 갑니다(`features/portfolio/pdf_extraction/service.py`
`_extract_background`). 활동 4개짜리 PDF는 마지막 활동이 끝날 때까지 화면에 아무것도 나오지
않습니다. v.3.5는 활동 1개가 끝날 때마다 화면에 흘려주기를 요구합니다.

## 2. 결정해야 할 것 두 가지

### 2-1. 전달 경로

| 안 | 구조 | 장점 | 단점 |
| --- | --- | --- | --- |
| **(A) AI SSE + 메인 프록시** ← **제안** | AI가 `text/event-stream` 을 내보내고 메인이 프록시 | 인터뷰 스트리밍이 이미 쓰는 방식([sse-streaming.md](sse-streaming.md)) — 프론트·메인 양쪽에 기존 처리 코드가 있음 | 업로드(POST)와 스트림을 한 요청으로 묶어야 함 |
| (B) 활동 단위 증분 콜백 | AI가 활동마다 `/internal/...` 콜백, 메인이 자체 채널로 중계 | 첨삭 도메인의 기존 콜백 방향과 일치 | 메인이 프론트로 흘릴 채널을 새로 만들어야 함. 왕복이 한 번 더 늘어 지연이 커짐 |

**제안: (A).** 인터뷰가 이미 같은 모양으로 동작하고 있어 프론트가 새로 배울 것이 없습니다.
(B)는 메인에 중계 채널을 새로 만드는 비용이 AI 쪽 변경보다 큽니다.

### 2-2. LLM 호출을 어떻게 쪼갤 것인가

현재는 PDF 전체를 한 번에 넘기는 **단일 structured output 호출**입니다
(`prompts/extraction.py:build_pdf_extraction_messages`).

| 안 | 방식 | 장점 | 단점 |
| --- | --- | --- | --- |
| **(가) 응답 스트리밍 파싱** ← **제안** | 지금의 1회 호출을 유지하되 응답 JSON을 흘려 받으며 `activities[]` 원소가 완성될 때마다 이벤트 발행 | 호출 1회 유지 → 비용·지연 그대로. 활동 경계 판단이 이미 프롬프트 안에 있음 | 부분 JSON 파서가 필요. 모델이 활동 순서대로 뱉는다는 전제에 기댐 |
| (나) 활동 단위 호출 분할 | 활동 경계를 먼저 찾고 활동마다 별도 호출 | 활동별 실패 격리가 쉬움 | LLM 호출이 최대 5회(경계 탐색 1 + 활동 4)로 늘어 비용·지연 증가 |

**제안: (가).** 다만 (가)는 활동 하나가 실패하면 그 지점부터 스트림이 끊깁니다 —
3-3의 부분 실패 정책이 이를 전제로 합니다.

## 3. 프로토콜 (제안)

### 3-1. 엔드포인트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `POST` | `/api/v1/corrections/{correction_id}/pdf-extraction/stream` | PDF 업로드와 활동 단위 추출 스트리밍 |

기존 `POST /api/v1/corrections/{correction_id}/pdf-extraction`(202 + 콜백)은 **당분간 유지**하고,
프론트가 스트리밍으로 완전히 넘어간 뒤 제거합니다. 전환 시점은 메인·프론트와 합의해 이 문서에
기록합니다.

응답 헤더는 [sse-streaming.md](sse-streaming.md)와 동일합니다
(`text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`).

### 3-2. 이벤트

| 이벤트명 | data 구조 | 설명 |
| --- | --- | --- |
| `extraction_started` | `{"type":"extraction_started"}` | 업로드 검증 통과, LLM 호출 시작 |
| `activity_completed` | `{"type":"activity_completed","index":0,"activity":{...}}` | 활동 1건 완성. `activity`는 기존 콜백의 `activities[]` 원소와 **동일한 camelCase 구조** (`activityName`·`detail`·`responsibility`·`problemSolving`·`learning`) |
| `extraction_completed` | `{"type":"extraction_completed","activityCount":3}` | 전체 완료. 이 이벤트 이후 스트림 종료 |
| `extraction_failed` | `{"type":"extraction_failed","error":{"code":"...","message":"..."}}` | 실패. 이 이벤트 이후 스트림 종료 |
| `ping` | `{"type":"ping","timestamp":"..."}` | 연결 유지 heartbeat, 10초 간격 |

`index`는 0부터 시작하고, 화면설계서의 활동 A/B/C 순서와 같습니다.
글자수 상한·활동 개수 상한(최대 4개)은 이벤트 발행 **전에** 적용되므로, 프론트는 받은 값을
그대로 화면에 넣으면 됩니다 ([CR-02](correction-v35-pr-plan.md) 참고).

### 3-3. 부분 실패 정책 (제안)

**이미 보낸 활동은 살립니다.** 2번째 활동에서 실패하면 1번째 `activity_completed`는 유효하고,
`extraction_failed`가 뒤따릅니다.

- 프론트: 이미 그린 활동은 남기고, 1-3 "텍스트 추출 실패" 상태를 함께 표시합니다.
- 메인: `extraction_failed`를 받으면 첨삭 상태를 실패로 두되, 이미 받은 활동은 저장합니다.
- '다시 시도하기'는 **처음부터 다시** 추출합니다(부분 재개 없음). 재시도 시 기존 활동은 버립니다.

> **백엔드 현황 (2026-08-21 `folioo-server` dev 확인)**: `internal-correction-result.facade.ts`
> `savePdfExtractionResult` 는 **전부-아니면-전무** 구조입니다. `activities` 배열을 한 번에 받아
> 외부 포트폴리오를 생성하고, 기존 correction item 을 지운 뒤 새로 저장하고, 마지막에
> `pdfExtractionStatus` 를 `GENERATED` 로 넘깁니다. 활동 1건씩 누적하는 경로가 없습니다.
>
> 따라서 부분 저장을 하려면 **메인에 증분 저장 경로를 새로 만들어야 합니다.** 그게 부담이면
> 대안은 "스트리밍은 화면 표시용으로만 쓰고, 저장은 지금처럼 마지막에 배치 콜백 1회"입니다 —
> 이 경우 AI는 SSE와 기존 콜백을 **둘 다** 내보내며, 프론트가 중간에 이탈하면 저장은 그대로
> 완료됩니다. 구현 부담이 가장 작은 안이라 **이쪽을 우선 검토할 것을 권합니다.**

### 3-4. 재연결·멱등

- 스트림이 끊기면 **재개하지 않고 처음부터 다시 요청**합니다. 추출은 수 초~수십 초 단위라
  재개 프로토콜의 복잡도가 이득보다 큽니다.
- `activity_completed`가 같은 `index`로 두 번 오는 경우는 없습니다. 그래도 메인·프론트는
  `index` 기준 **upsert**로 처리해 중복에 안전하게 둡니다.

## 4. 승인 체크리스트

- [ ] 2-1 전달 경로: (A) SSE + 프록시로 합의
- [ ] 2-2 호출 분할: (가) 응답 스트리밍 파싱으로 합의
- [ ] 3-2 이벤트 이름·payload 확정
- [ ] 3-3 부분 실패 시 메인의 부분 저장 여부 확정
      (증분 저장 경로 신설 vs 스트리밍은 표시용 + 저장은 기존 배치 콜백 유지)
- [ ] 기존 `POST .../pdf-extraction`(202 + 콜백) 제거 시점 합의
