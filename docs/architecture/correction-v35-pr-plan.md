# 포트폴리오 첨삭 v.3.5 대응 PR 분할 계획

> **범위**: 화면설계서 v.3.5 [포트폴리오 첨삭] 섹션과 folioo-ai(AI 서버) 구현의 차이를
> PR 단위로 쪼갠 실행 계획.
>
> 기준 문서: [화면설계서 v.3](https://www.figma.com/design/190ub4CA8flVwOniEWAXRd/화면설계서-v.3?node-id=1071-1645)
> (node `1071-1645`) — 좌상단 "v.3.4 대비 수정된 사항" 노트 2번 항목과
> [포트폴리오 첨삭] 섹션의 화면 주석.
>
> **2026-08-21 현황**: 통합 브랜치 `feat/correction-v35` 에 CR-01·CR-02·CR-03·CR-06·CR-07 커밋 완료.
> 메인 백엔드(`Teamie71/folioo-server` dev)를 직접 대조해 5·6절을 채웠습니다 — **백엔드 쪽
> v.3.5 반영은 아직 0건**이고, 상태 전이 불일치 2건을 새로 발견했습니다.
> CR-04 계약 문서는 초안까지 작성했고 메인 백엔드 승인을 기다립니다.
> CR-05(스트리밍 구현)는 그 승인이 나야 착수합니다. 아래 상태 표시는 이 시점의 스냅샷입니다.

---

## 1. 분할 원칙

1. **한 PR = 한 리뷰 관심사.** 상수 조정과 프로토콜 신설을 한 PR에 섞지 않습니다.
2. **머지된 dev는 항상 초록.** 각 PR은 자체 테스트를 포함하고 `ruff check .`·
   `ruff format --check .`·`pytest`를 통과한 상태로 머지합니다.
3. **메인 백엔드/프론트 몫은 PR 대상이 아닙니다.** 이용권 차감 제거, 첨삭 30개 제한,
   비로그인 게이트는 여기서 외부 의존으로만 추적합니다 (5절).
4. **계약이 필요한 변경은 문서를 먼저.** CR-04(스트리밍 계약)가 합의되기 전에는
   CR-05를 시작하지 않습니다.
5. **작은 상수 변경(CR-01~03)을 먼저 처리**해 스트리밍 작업이 리베이스 부담을 지지 않게 합니다.

## 2. 머지 전략

**전부 통합 브랜치 `feat/correction-v35`에 모았다가 마지막에 한 번에 dev로 머지합니다.**
CR-01~CR-03·CR-06이 각각 하루 이내 크기이긴 하지만, 첨삭 v.3.5는 프론트·메인 백엔드와
동시에 나가야 하는 한 덩어리의 스펙 변경입니다. 상수만 먼저 dev에 들어가면 활동 개수
4개와 스트리밍 부재가 섞인 중간 상태가 배포될 수 있어, 스펙 단위로 묶어서 올립니다.

```text
dev ──── feat/correction-v35
              ├─ CR-01  활동 개수 5→4
              ├─ CR-02  카테고리 글자수 상한
              ├─ CR-03  강조 포인트 선택 항목화
              ├─ CR-06  대기 상태·재시도 경로 정리
              ├─ CR-04  스트리밍 계약 문서
              ├─ CR-05  활동 단위 스트리밍 구현
              └─ (전부 끝나면 dev로 한 번에)
```

각 CR은 `feat/correction-v35`를 향한 개별 PR로 올려 리뷰 단위를 유지하고, 마지막
dev 머지 PR 본문에 `Closes` 를 모아 이슈를 한 번에 닫습니다. (GitHub은 **기본
브랜치(dev)로 머지될 때만** 이슈를 닫으므로, 통합 브랜치를 향한 PR의 `Closes`는
발동하지 않습니다.)

브랜치 이름은 레포 관례를 따릅니다 — `feat/{issue}-{slug}`, `docs/…`, `fix/…`.
PR 제목은 이슈와 같은 대괄호 접두사를 씁니다 — `[Feat]`, `[Fix]`, `[Docs]`.

## 3. PR 목록

크기 기준: **S** 하루 이내 · **M** 2~3일 · **L** 3일 이상. ✅ 는 통합 브랜치 커밋 완료, 🟡 는 초안 작성 후 외부 승인 대기입니다.

| # | PR | 근거 (피그마) | 의존 | 크기 |
| --- | --- | --- | --- | --- |
| ✅ CR-01 | 활동 최대 개수 5 → 4 | "앞에서부터 최대 4개의 활동까지 텍스트를 추출한다" | — | S |
| ✅ CR-02 | 카테고리별 글자수 상한을 추출 파이프라인에 반영 | "[글자수 제한] 상세정보·배운 점 300자 / 담당업무·문제해결 700자" | — | M |
| ✅ CR-03 | 강조 포인트 선택 항목화 | [기업 분석] 화면의 `강조 포인트`에 필수(*) 표시 없음 | — | S |
| 🟡 CR-04 | 텍스트 추출 활동 단위 스트리밍 **계약 정의** | "활동 단위로 스트리밍 한다" (1-4 텍스트 추출 스트리밍) | — | S |
| CR-05 | 활동 단위 스트리밍 **구현** | 동일 | CR-01, CR-04 | L |
| ✅ CR-06 | 대기 상태 메시지·추출 재시도 경로 정리 | [첨삭 생성 대기] 화면 문구, 1-3 "다시 시도하기" | — | S |
| ✅ CR-07 | 첨삭 상태에 `RAG_FAILED` 추가 | 백엔드 대조에서 발견한 500 결함 (6절) | — | S |

### 의존 그래프

```text
CR-01 ─┐
       ├─→ CR-05
CR-04 ─┘

CR-02, CR-03, CR-06 : 독립
```

## 4. PR 상세

### CR-01 — 활동 최대 개수 5 → 4

**브랜치**: `feat/{issue}-correction-max-activities-4` (base: `feat/correction-v35`) · **크기** S

피그마 [포트폴리오 업로드] 주석은 "앞에서부터 최대 4개의 활동까지 텍스트를 추출한다",
업로드 카드 안내 문구는 "최대 10MB의 파일, 최대 4개의 활동 첨삭이 가능해요."입니다.
현재 코드는 전 구간이 5 기준입니다.

**구현 체크리스트**

- [x] `features/portfolio/pdf_extraction/schemas.py:31` — `PdfExtractionResult.activities` `max_length=5` → `4`
- [x] `features/portfolio/pdf_extraction/service.py:142` — `result.activities[:5]` → `[:4]`
- [x] `features/portfolio/pdf_extraction/prompts/classification.md`
  - `:76` "1번째~5번째 활동만 선택" → 4번째까지
  - `:78-79` "배열의 길이는 최대 5개", "활동이 5개 미만인 경우" → 4 기준
  - `:177-182` 순서 매핑에서 `activities[4]` 줄 제거
- [x] `features/correction/service.py:305-306` — `len(portfolio_ids) > 5` → `> 4`, 메시지도 "최대 4개"
- [x] 테스트 갱신: `tests/test_features/test_portfolio/test_pdf_extraction_schemas.py:45`,
      `test_pdf_extraction_prompt.py:21`, `test_pdf_extraction_service.py:207`

**Definition of Done**

- [x] 활동 5개짜리 PDF 추출 결과가 앞 4개로 잘리는지 검증
- [x] `portfolio_ids` 5개 요청이 `ValueError("포트폴리오는 최대 4개까지 허용됩니다.")`로 막히는지 검증
- [x] 프롬프트 문자열 단정 테스트가 4 기준으로 통과

**리스크 / 메모**

- `max_length`는 LLM structured output 스키마에 그대로 실리므로, 프롬프트(`classification.md`)와
  스키마를 함께 바꾸지 않으면 모델이 5개를 반환해 검증에서 실패합니다. **반드시 한 PR에서 함께.**

---

### CR-02 — 카테고리별 글자수 상한을 추출 파이프라인에 반영

**브랜치**: `feat/{issue}-correction-category-char-limits` (base: `feat/correction-v35`) · **크기** M

피그마 [포트폴리오 업로드] 주석 `[글자수 제한]`:

- 상세정보·배운 점: 최대 300자 (한국어·영어·공백·특수문자)
- 담당업무·문제해결: 최대 700자
- 초과 시 글자수 카운터가 붉게 표시되고 '다음으로' 버튼이 비활성 상태를 유지

즉 **초과분을 만든 책임은 AI 쪽에 있고, 사용자가 손으로 줄여야 진행됩니다.** 현재 추출
프롬프트·후처리 어디에도 카테고리별 상한이 없어 초과 산출이 그대로 나갑니다.

**구현 체크리스트**

- [x] `features/portfolio/config/` 또는 `pdf_extraction` 전용 설정에 카테고리별 상한 상수 정의
      (하드코딩 대신 설정값 — `features/correction/config/correction.yaml`의
      `company_insight_max_length` 선례를 따름)
- [x] `classification.md`에 카테고리별 글자수 상한 규칙 추가 (원문 복사 원칙과 충돌하므로
      "상한을 넘으면 뒤쪽 문장을 버리고 앞에서부터 채운다" 같은 결정 규칙을 명시)
- [x] `PdfExtractionService._validate_result`에 카테고리 합계 길이 검증 추가 —
      초과 시 잘라내기(truncate)로 처리할지, 경고 로그만 남길지 결정 (아래 열린 질문)
- [x] `common/utils/text.py`의 기존 헬퍼(`is_within_char_limit`·`truncate_to_char_limit`·`get_char_overflow`) 재사용
- [x] 테스트: 카테고리별 초과 입력이 상한 이하로 정리되는지

**Definition of Done**

- [x] 300/700자를 넘는 추출 결과가 콜백 전에 상한 이하로 정리됨을 검증
- [x] 상한 값이 설정에서 주입되고 테스트가 설정을 오버라이드해 동작함을 검증

**확정 사항** (2026-08-21)

1. **300/700자는 카테고리 전체 합계** 기준입니다. 텍스트 에어리어 하나에 들어가는 모든
   불릿을 개행으로 이은 문자열의 길이로 셉니다. **프론트 카운터가 같은 기준인지는 배포 전
   반드시 대조해야 합니다** — 기준이 어긋나면 AI가 상한 이내로 보내도 화면에서 초과로 표시됩니다.
2. **초과 시 AI가 자릅니다.** 앞에서부터 불릿을 채우고 남는 뒤쪽 불릿은 버리며, 첫 불릿
   하나가 상한을 넘는 경우에만 남은 예산만큼 잘라서 살립니다. 잘렸다는 사실은 콜백 응답에
   표시하지 않습니다(프론트 글자수 카운터로 이미 드러납니다).
   → **원문 뒤쪽이 소실되는 트레이드오프를 받아들인 결정입니다.**
3. JD 1000자 제한은 **AI 레포 작업이 아닙니다.** JD는 메인이 저장하고 AI는
   `correction["jobDescription"]`를 읽기만 합니다(`features/correction/service.py:107`).
   `app/schemas/correction.py`의 `CreateCorrectionRequest`는 어느 라우터에도 연결돼 있지 않은
   사문화 스키마이므로, 여기에 `max_length`를 다는 것은 실효가 없습니다 (CR-06 결론 참고).

---

### CR-03 — 강조 포인트 선택 항목화

**브랜치**: `fix/{issue}-emphasis-points-optional` (base: `feat/correction-v35`) · **크기** S

피그마 [기업 분석] 화면에서 `기업 분석 정보`에는 필수(*)가 붙지만 `강조 포인트`에는 없고,
설명도 "…강조하고 싶은 역량이나 기술 등이 **있다면** 작성해주세요."입니다.
현재 `UpdateEmphasisPointsRequest.emphasis_points`는 `min_length=1`이라 빈 값을 422로 거부합니다.

**구현 체크리스트**

- [x] `app/schemas/correction.py:63` — `min_length=1` 제거 (빈 문자열 허용)
- [x] `features/correction/service.py`의 `emphasis_points` 빈 값 경로 확인 —
      이미 `correction.get("highlightPoint") or ""`로 빈 문자열을 다루므로 generator 프롬프트가
      빈 강조 포인트에서 자연스러운 출력을 내는지 확인
- [x] 테스트: 빈 강조 포인트로 PATCH 200, 빈 강조 포인트로 첨삭 생성이 성공

**Definition of Done**

- [x] `PATCH /corrections/{id}/emphasis-points`에 `{"emphasis_points": ""}` 요청이 200
- [x] 강조 포인트가 빈 상태의 첨삭 생성 스냅샷 테스트 통과

---

### CR-04 — 텍스트 추출 활동 단위 스트리밍 계약 정의

**브랜치**: `docs/{issue}-pdf-extraction-streaming-contract` (base: `feat/correction-v35`) · **크기** S

피그마 [포트폴리오 업로드] 주석: "**1개의 활동 하의 포트폴리오 텍스트 추출이 완료되면,
텍스트 추출 스트리밍(1-4)을 표시하며, 활동 단위로 스트리밍 한다.**"

현재는 배치 추출이 전부 끝난 뒤 `complete_pdf_extraction` 콜백을 **1회**만 보냅니다
(`features/portfolio/pdf_extraction/service.py:69-95`, `common/clients/correction_client.py:170`).
활동 단위 전달 경로가 아예 없습니다.

이 PR은 **코드 변경 없이 계약 문서만** 만듭니다. 메인 백엔드와 합의된 뒤 CR-05를 시작합니다.

**구현 체크리스트**

- [x] `docs/architecture/pdf-extraction-streaming.md` 신규 작성 (제안 상태)
  - [x] 전달 방식 제안: **(A) AI SSE + 메인 프록시** — 인터뷰 스트리밍과 같은 구조
  - [x] LLM 호출 분할 제안: **(가) 응답 스트리밍 파싱** — 호출 1회 유지
  - [x] 이벤트 스키마: `extraction_started` / `activity_completed` /
        `extraction_completed` / `extraction_failed` / `ping`
  - [x] 부분 실패 정책: 이미 보낸 활동은 살리고 재시도는 처음부터 (메인 확인 필요)
  - [x] 재연결·멱등: 재개 없음, `index` 기준 upsert
- [ ] **메인 백엔드 담당자 리뷰 승인** (외부 의존, 5절) — CR-05 착수 조건

**Definition of Done**

- [ ] 문서에 이벤트 스키마와 실패/재연결 정책이 확정되고, 메인 백엔드 리뷰가 승인됨

**리스크 / 메모**

- 현재 LLM 호출은 PDF 전체를 한 번에 넘기는 **단일 structured output 호출**입니다
  (`prompts/extraction.py:build_pdf_extraction_messages`). 활동 단위로 흘리려면 모델 응답
  자체를 스트리밍 파싱하거나, 활동 단위로 호출을 쪼개야 합니다 — **어느 쪽인지가 CR-05의
  크기를 좌우하므로 이 문서에서 함께 결정합니다.**

---

### CR-05 — 활동 단위 스트리밍 구현

**브랜치**: `feat/{issue}-pdf-extraction-streaming` (base: `feat/correction-v35`) · **의존** CR-01, CR-04 · **크기** L

**구현 체크리스트**

- [ ] CR-04에서 정한 방식대로 추출 실행부를 스트리밍 구조로 전환
      (`PdfExtractionService._extract_background` 재작성)
- [ ] `CorrectionClient`에 활동 단위 이벤트 전송 메서드 추가 (또는 SSE 엔드포인트 신설)
- [ ] 활동 1건 완료 시점의 후처리(`_format_activities_for_callback`·중복 제거·`no` 재번호)를
      **누적 상태 기준**으로 다시 설계 — 현재는 전체 리스트를 한 번에 훑는 전제
- [ ] 4개 도달 시 조기 종료 (CR-01의 상한을 스트리밍 경로에서도 보장)
- [ ] 부분 실패 시 CR-04에서 정한 정책대로 처리
- [ ] 테스트: 활동 3건 스트리밍 순서 검증, 2번째에서 실패하는 시나리오, 중복 활동명 스킵

**Definition of Done**

- [ ] 활동이 완료될 때마다 이벤트가 1건씩 나가고, 마지막에 완료 이벤트가 정확히 1회 발생
- [ ] 기존 배치 콜백 소비자가 깨지지 않음 (또는 메인과 합의된 전환 시점이 문서화됨)
- [ ] 실패 시나리오에서 `fail_pdf_extraction`이 정확히 1회 호출

---

### CR-06 — 대기 상태 메시지·추출 재시도 경로 정리

**브랜치**: `chore/{issue}-correction-status-cleanup` (base: `feat/correction-v35`) · **크기** S

**결론** (2026-08-21)

- [x] **`progress_message`는 `None` 유지** (`app/api/v1/correction.py:113`).
      [첨삭 생성 대기] 화면 문구 "AI 컨설턴트가 포트폴리오 첨삭을 진행 중이에요.
      페이지를 벗어나도 괜찮아요."는 상태에 따라 바뀌지 않는 **고정 카피**입니다.
      AI가 매 폴링마다 같은 문자열을 실어 보낼 이유가 없으므로 프론트 고정 문구로 둡니다.
      상태별로 다른 문구가 필요해지면 그때 이 필드를 채웁니다.
- [x] **사문화 스키마는 그대로 둡니다.** `CreateCorrectionRequest`/`CreateCorrectionResponse`는
      어느 라우터에도 연결돼 있지 않지만(`app/schemas/correction.py:13-27`), 메인이 나중에
      AI로 첨삭 생성 요청을 보내는 경로가 생길 수 있어 유지합니다.
      **주의**: 연결돼 있지 않으므로 여기에 검증(`max_length` 등)을 추가해도 실효가 없습니다.
- [ ] **추출 재시도 경로는 프론트 확인 대기** → 5절 외부 의존으로 옮깁니다.
      1-3 "다시 시도하기"가 `POST /corrections/{id}/pdf-extraction` 재호출(파일 재업로드
      포함)로 커버되는지 확인이 필요합니다. 현재 `/retry`는 첨삭 생성 전용입니다.

**Definition of Done**

- [x] 세 항목이 각각 "구현" 또는 "이대로 둔다 + 근거"로 결론나고 코드/문서에 반영

---

### CR-07 — 첨삭 상태에 `RAG_FAILED` 추가

**브랜치**: `fix/{issue}-correction-rag-failed-status` (base: `feat/correction-v35`) · **크기** S

백엔드 대조 중 발견한 결함입니다. 피그마 변경 사항이 아니라 기존 동작 버그입니다.

- [x] `features/correction/schemas.py` `CorrectionStatus` 에 `RAG_FAILED = "rag_failed"` 추가
- [x] `GET /corrections/{id}/status` 가 `RAG_FAILED` 응답에서 200 을 반환하는지 테스트

**Definition of Done**

- [x] 메인이 `RAG_FAILED` 를 반환할 때 상태·결과 조회가 500 대신 정상 응답

**남은 것**: 이 상태에서 재시도하는 경로는 백엔드 전이 규칙이 막고 있습니다 (6절 문제 2).

---

## 5. 외부 의존 — 메인 백엔드(`Teamie71/folioo-server`) 확인 결과

2026-08-21 기준 `dev`(`f9b145d`)를 직접 확인했습니다. **첨삭 v.3.5 관련 백엔드 변경은
아직 하나도 반영돼 있지 않습니다.** 열려 있는 PR도 전부 경험 정리 맵 작업입니다.

| 항목 | 피그마 v.3.5 | 백엔드 현재 값 | 위치 |
| --- | --- | --- | --- |
| 활동 블록 최대 개수 | 4 | **5** | `portfolio.entity.ts` `MAX_EXTERNAL_PORTFOLIO_BLOCKS`, `external-portfolio.facade.ts:101`, `portfolio-correction.facade.ts:168`, `error-code.ts:225` |
| 첨삭 저장 개수 상한 | 30 | **15** | `portfolio-correction.entity.ts` `MAX_CORRECTIONS_PER_USER`, `error-code.ts:220` |
| JD 글자수 | 1000 | **700** | `portfolio-correction.entity.ts` `jobDescription` 컬럼, `portfolio-correction.dto.ts:73` |
| 상세정보 / 배운 점 | 300 | **400** | `external-portfolio.dto.ts` `UpdatePortfolioBlockReqDTO` |
| 담당업무 / 문제해결 | 700 | **400** | 동일 |
| 이용권 차감 | 제거 | **차감 유지** | `portfolio-correction.facade.ts:37` `consumeTicket(userId, TicketType.PORTFOLIO_CORRECTION)` |
| 활동 단위 스트리밍 | 필요 | **없음** (배치 콜백 1회) | `internal-correction-result.facade.ts` `savePdfExtractionResult` |

**AI 레포와 이미 맞는 것**

- 기업 분석 2000자: `COMPANY_INSIGHT_MAX_LENGTH = 2000` ↔ `correction.yaml` `company_insight_max_length`. **일치** (CR-06에서 열려 있던 질문 해소)
- PDF 10MB 제한: `external-portfolio-extract-request-parser.service.ts` `MAX_PDF_SIZE_BYTES` ↔ AI 동일
- 카테고리 글자수 세는 방식: 백엔드가 불릿을 `\n` 으로 이어 저장(`mapActivity` `joinLines`) → **AI의 "카테고리 전체 합계, 개행 1자" 계산과 동일**. CR-02의 열린 질문 1이 해소됐습니다.

**AI가 맞춰야 했던 것 (CR-02 보정 완료)**

- 문제해결만 백엔드가 `#{no}\n상황: …\n전략: …\n이유: …` 로 라벨을 붙여 `\n\n` 으로 잇습니다.
  항목당 17자 + 항목 사이 2자가 더 붙으므로 필드 길이만 세면 화면 카운터가 초과로 표시됩니다.
  → AI 예산 계산에 오버헤드를 포함했습니다. **백엔드가 이 렌더링을 바꾸면 AI도 함께 고쳐야 합니다.**

**메인 백엔드에 요청할 것**

1. 위 표의 상한값 6종을 v.3.5 기준으로 변경
2. `consumeTicket` 호출 제거
3. `UpdatePortfolioBlockReqDTO` 의 400자를 카테고리별 300/700 으로 분리
4. 상태 전이 규칙 보완 (아래 6절)
5. 활동 단위 스트리밍 수신 (CR-04 계약 합의)

**프론트에 확인할 것**

- 추출 실패(1-3) '다시 시도하기'가 `POST /corrections/{id}/pdf-extraction` 재호출(파일 재업로드 포함)로 커버되는지
- 글자수 카운터가 저장된 문자열 기준인지 (문제해결은 라벨 포함 여부가 갈림)

## 6. 상태 전이 불일치 (신규 발견)

백엔드 `portfolio-correction.service.ts` `validateStatusTransition` 의 허용 표는 다음과 같습니다.

```text
NOT_STARTED     → DOING_RAG
DOING_RAG       → COMPANY_INSIGHT
COMPANY_INSIGHT → GENERATING
GENERATING      → DONE
FAILED          → GENERATING
RAG_FAILED      → (없음)
DONE            → (없음)
```

**문제 1 — RAG 단계 실패는 재시도가 불가능합니다.**
AI의 `CorrectionService.retry` 는 저장된 RAG 데이터가 없거나 인사이트가 없으면
`FAILED → DOING_RAG` 로 전이하려 합니다(`features/correction/service.py` 경로 2·3).
백엔드는 `FAILED → [GENERATING]` 만 허용하므로 이 두 경로는 항상 422(AI 쪽 409)로 막힙니다.
→ **백엔드에 `FAILED → DOING_RAG` 허용을 요청해야 합니다.**

**문제 2 — `RAG_FAILED` 상태는 빠져나갈 길이 없습니다.**
메인이 AI 서버 호출에 실패하면 `RAG_FAILED` 로 두는데, 이 상태에서 나가는 전이가 없어
사용자가 첨삭을 되살릴 수 없습니다. → **`RAG_FAILED → DOING_RAG` 허용 요청.**

**AI 쪽 대응 (완료)**: `CorrectionStatus` 에 `RAG_FAILED` 가 없어
`GET /corrections/{id}` 와 `/status` 가 응답 검증에서 터져 **500** 을 반환하고 있었습니다.
enum 에 값을 추가해 조회는 정상화했습니다. 재시도 자체는 위 백엔드 변경이 있어야 풀립니다.

## 7. 이미 명세와 일치하는 부분 (변경 없음)

착수 전 재확인용 체크리스트입니다. 아래는 v.3.5 명세와 현재 코드가 **이미 맞습니다**.

- `source_type="EXTERNAL"` 고정 (`features/portfolio/pdf_extraction/service.py:94`)
  ↔ "internal 포트폴리오 선택 불가, external만 사용 가능"
- 10MB 업로드 제한 (`app/api/v1/correction.py:26`, `pdf_extraction/service.py:19`)
- 표지·**자기소개**·이력 페이지 제외 (`classification.md:60`)
  ↔ "자기소개 페이지는 첨삭되지 않아요"
- 4개 카테고리 매핑 `description`/`contributions`/`achievements`/`insights`
  ↔ 상세정보 / 담당업무 / 문제해결 / 배운 점 (`features/correction/service.py:26-31`)
- 라인 타입 `reduce`/`emphasize`/`keep` ↔ "축소 또는 제외" / "구체화하여 강조" / 무표시
- 카테고리가 비어도 통과 (`features/correction/generator.py:222`의 `line_count > 0` 가드)
  ↔ "첨삭할 내용이 존재하지 않아요" 빈 상태
- `overall_summary` ↔ [첨삭 결과]의 `총평` 탭
