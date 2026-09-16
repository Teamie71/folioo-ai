# PDF 텍스트 추출 활동 단위 스트리밍 계약

> **상태**: AI 서버 구현 완료 (CR-05). **메인 서버 변경 없이 배포 가능합니다.**
> 프론트가 스트림에 닿으려면 메인이 SSE 프록시 라우트만 추가하면 됩니다.
>
> **근거**: 화면설계서 v.3.5 [포트폴리오 첨삭 - 포트폴리오 업로드] 주석 1
> "1개의 활동 하의 포트폴리오 텍스트 추출이 완료되면, 텍스트 추출 스트리밍(1-4)을 표시하며,
> **활동 단위로 스트리밍** 한다."
>
> 관련: [첨삭 v.3.5 PR 분할 계획](correction-v35-pr-plan.md) CR-04·CR-05,
> [SSE 스트리밍 프로토콜](sse-streaming.md)

---

## 1. 배경

```text
프론트 ──POST /api/v1/corrections/{id}/pdf-extraction (파일)──→ AI
                                                                 │ (background task)
                                                                 │ PDF 전체를 LLM 1회 호출
                                                                 ↓
메인 ←──POST /internal/corrections/{id}/pdf-extraction-result────┘
        {"status":"completed","activities":[...4개 전부...]}
```

위는 **기존 배치 경로**입니다. 추출이 전부 끝난 뒤에 한 번 콜백이 가므로, 활동 4개짜리 PDF는
마지막 활동이 끝날 때까지 화면에 아무것도 나오지 않습니다. v.3.5 는 활동 1개가 끝날 때마다
화면에 흘려주기를 요구합니다.

새로 추가한 스트리밍 경로는 이렇게 동작합니다.

```text
프론트 ──POST .../pdf-extraction/stream (파일)──→ AI
       ←──── extraction_started ──────────────────┤
       ←──── activity_completed (index 0) ────────┤  활동이 완성될 때마다
       ←──── activity_completed (index 1) ────────┤
                                                  │
메인 ←──POST /internal/.../pdf-extraction-result──┤  저장은 마지막에 1회 (기존과 동일)
       ←──── extraction_completed ────────────────┘
```

## 2. 선택한 방식

### 2-1. 전달 경로

| 안 | 구조 | 장점 | 단점 |
| --- | --- | --- | --- |
| **(A) AI SSE + 메인 프록시** ← **채택** | AI가 `text/event-stream` 을 내보내고 메인이 프록시 | 인터뷰 스트리밍이 이미 쓰는 방식([sse-streaming.md](sse-streaming.md)) — 프론트·메인 양쪽에 기존 처리 코드가 있음 | 업로드(POST)와 스트림을 한 요청으로 묶어야 함 |
| (B) 활동 단위 증분 콜백 | AI가 활동마다 `/internal/...` 콜백, 메인이 자체 채널로 중계 | 첨삭 도메인의 기존 콜백 방향과 일치 | 메인이 프론트로 흘릴 채널을 새로 만들어야 함. 왕복이 한 번 더 늘어 지연이 커짐 |

**채택: (A).** 인터뷰가 이미 같은 모양으로 동작하고 있어 프론트가 새로 배울 것이 없습니다.
(B)는 메인에 중계 채널을 새로 만드는 비용이 AI 쪽 변경보다 큽니다.
`interleave_ping_events` 를 `common/sse/ping.py` 로 옮겨 인터뷰 스트림과 공유합니다.

### 2-2. LLM 호출을 어떻게 쪼갤 것인가

현재는 PDF 전체를 한 번에 넘기는 **단일 structured output 호출**입니다
(`prompts/extraction.py:build_pdf_extraction_messages`).

| 안 | 방식 | 장점 | 단점 |
| --- | --- | --- | --- |
| **(가) 응답 스트리밍 파싱** ← **채택** | 지금의 1회 호출을 유지하되 응답 JSON을 흘려 받으며 `activities[]` 원소가 완성될 때마다 이벤트 발행 | 호출 1회 유지 → 비용·지연 그대로. 활동 경계 판단이 이미 프롬프트 안에 있음 | 부분 JSON 파서가 필요. 모델이 활동 순서대로 뱉는다는 전제에 기댐 |
| (나) 활동 단위 호출 분할 | 활동 경계를 먼저 찾고 활동마다 별도 호출 | 활동별 실패 격리가 쉬움 | LLM 호출이 최대 5회(경계 탐색 1 + 활동 4)로 늘어 비용·지연 증가 |

**채택: (가).** `ActivityJsonStreamParser` 가 부분 JSON 에서 완성된 `activities[]` 원소를
하나씩 떼어 냅니다. 문자열 안의 중괄호와 이스케이프를 구분해 원소 경계를 잘못 잡지 않습니다.

**대가**: structured output 을 쓰지 않으므로 스키마 강제가 API 레벨에서 사라집니다.
프롬프트의 출력 형식 규칙과 원소별 `PdfActivity` 검증으로 대신하고, 스키마에 어긋난 원소는
건너뜁니다. **기존 배치 엔드포인트는 structured output 을 그대로 쓰므로 영향이 없습니다.**

## 3. 프로토콜

### 3-1. 엔드포인트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `POST` | `/api/v1/corrections/{correction_id}/pdf-extraction/stream` | PDF 업로드와 활동 단위 추출 스트리밍 |

기존 `POST /api/v1/corrections/{correction_id}/pdf-extraction`(202 + 콜백)은 **그대로 유지**됩니다.
프론트가 스트리밍으로 완전히 넘어간 뒤 제거 시점을 정합니다.

업로드 크기 초과는 스트림을 열기 전에 **400** 으로 끊고, 그 외 파일 검증 실패
(빈 파일 · PDF 아님)는 스트림 안에서 `extraction_failed` 이벤트로 알립니다.

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

### 3-3. 부분 실패 정책

**화면에는 이미 보낸 활동이 남고, 저장은 전부 버려집니다.**
2번째 활동에서 실패하면 1번째 `activity_completed` 는 이미 전달된 상태로 두고
`extraction_failed` 가 뒤따릅니다. 동시에 AI 는 메인에 **실패 콜백**을 보내므로
`pdfExtractionStatus` 는 `FAILED` 가 되고 부분 저장은 일어나지 않습니다.

- 프론트: 이미 그린 활동은 남기되 1-3 "텍스트 추출 실패" 상태를 함께 표시합니다.
  **저장되지 않았으므로 새로고침하면 사라집니다.**
- '다시 시도하기'는 **처음부터 다시** 추출합니다(부분 재개 없음).

> **왜 부분 저장을 하지 않는가** (2026-08-21 `folioo-server` dev 확인):
> `internal-correction-result.facade.ts` `savePdfExtractionResult` 는 **전부-아니면-전무**
> 구조입니다. `activities` 배열을 한 번에 받아 외부 포트폴리오를 생성하고, 기존 correction item
> 을 지운 뒤 새로 저장하고, 마지막에 `pdfExtractionStatus` 를 `GENERATED` 로 넘깁니다.
> 활동 1건씩 누적하는 경로가 없습니다.
>
> 부분 저장을 하려면 메인에 증분 저장 경로를 새로 만들어야 하므로, **스트림은 표시용으로만
> 쓰고 저장은 기존 배치 콜백 1회로** 두었습니다. 메인 서버 변경 없이 배포할 수 있는 대신,
> 중간 실패 시 화면에 보이던 활동이 저장되지 않는 것을 감수합니다.

### 3-4. 재연결·멱등

- 스트림이 끊기면 **재개하지 않고 처음부터 다시 요청**합니다. 추출은 수 초~수십 초 단위라
  재개 프로토콜의 복잡도가 이득보다 큽니다.
- `activity_completed`가 같은 `index`로 두 번 오는 경우는 없습니다. 그래도 프론트는
  `index` 기준 **upsert**로 처리해 중복에 안전하게 둡니다.
- 활동명이 중복인 활동은 이벤트를 내보내지 않습니다(여러 페이지에 걸친 같은 활동).
  따라서 `index` 는 **채택된 활동의 순번**이며 LLM 이 뱉은 순번과 다를 수 있습니다.

## 4. 남은 협의 사항

구현은 끝났고, 아래는 **켜기 위해** 필요한 것들입니다.

- [ ] **메인**: `POST /api/v1/corrections/{id}/pdf-extraction/stream` 로 향하는 SSE 프록시
      라우트 추가. 인터뷰 스트림과 같은 방식이면 됩니다.
- [ ] **프론트**: 이벤트 이름·payload 확인. 다른 이름을 원하면 rename 으로 대응 가능합니다.
- [ ] **프론트**: 중간 실패 시 화면에 남은 활동이 저장되지 않는다는 점을 UX 로 어떻게 다룰지
- [ ] 기존 `POST .../pdf-extraction`(202 + 콜백) 제거 시점 합의

## 5. 구현 위치

| 역할 | 파일 |
| --- | --- |
| SSE 엔드포인트 | `app/api/v1/correction.py` `stream_pdf_extraction` |
| 스트림 오케스트레이션 · 콜백 | `features/portfolio/pdf_extraction/service.py` `stream_extraction` |
| LLM 토큰 스트리밍 | `features/portfolio/pdf_extraction/generator.py` `extract_stream` |
| 부분 JSON 파서 | `features/portfolio/pdf_extraction/streaming.py` |
| ping 인터리빙 (공용) | `common/sse/ping.py` |
