# 경험 정리 에이전트 시나리오 테스트 결과

- 실행일: 2026-08-29, 재검증 2026-08-30
- 기준 브랜치: `fix/structure-template-decomposition`
- 기준 변경: `4c6740f` 이후 PDF 구조 계층 보정 포함
- 결과: 8개 시나리오 통과

## 테스트 범위

실제 경험 정리 에이전트의 LangGraph, target activity, content filter, validate,
coordinator와 in-memory commit store를 사용했다. 결과를 반복해서 재현할 수 있도록
OpenRouter LLM 응답과 메인 백엔드 API만 고정된 테스트 대역으로 바꿨다. 따라서 아래
결과는 에이전트 내부 처리와 커밋 계약을 검증하지만, 실제 모델의 답변 품질이나 메인
백엔드와의 네트워크 연동까지 검증한 결과는 아니다.

| 번호 | 상황 | 실행 범위 | 결과 |
| --- | --- | --- | --- |
| 1 | 일반 채팅으로 신규 블록 생성 | 전체 LangGraph | PASS |
| 2 | 메시지 없이 파일만 업로드 | 활동 선택 + coordinator + commit | PASS |
| 3 | 기존 내용 제외 후 신규 내용만 반영 | content filter + coordinator + commit | PASS |
| 4 | 신규 블록을 후속 질문의 anchor로 사용 | coordinator + commit + gap | PASS |
| 5 | gap 분석 실패 | coordinator의 실패 복구 경로 | PASS |
| 6 | 활성 gap에 답변하여 기존 블록 수정 | coordinator + update commit | PASS |
| 7 | 검증 보정 한도 초과 | validate 실패 경로 | PASS |

## 1. 일반 채팅으로 신규 블록 생성

전체 LangGraph를 실행한 기본 시나리오다.

입력:

> 결제 오류 원인을 분석하고 재시도 로직을 추가해 장애를 줄였다.

결과 응답:

> 내용을 분석하여 경험을 정리했어요.
>
> - 문제 해결 아래 1개의 블록 생성

후속 응답:

> 더 정리하고 싶으신 내용이 있나요?

커밋 결과:

```json
{
  "previous_version": 1,
  "map_version": 2,
  "applied": [
    {
      "item_id": "demo_1",
      "block_id": "1001",
      "path": "교내 커머스 리뉴얼 > 문제 해결"
    }
  ],
  "dropped": []
}
```

블록 생성 결과:

```text
[b_1] 문제 해결
  [기존] 기존 문제 해결 내용
  [1001] 결제 오류 원인 분석 → 재시도 로직 추가로 장애 감소
```

판정: 새 블록이 `문제 해결` 카테고리 아래 생성됐고, 응답의 생성 개수와 실제
커밋 개수가 일치했다.

## 2. 메시지 없이 파일만 업로드

사용자 메시지는 비어 있고, 업로드 파일에서 추출한 다음 텍스트만 있는 상황을
재현했다.

파일에서 추출한 입력:

> 사용자 행동 데이터를 분석해 협업 필터링 모델의 추천 정확도를 12% 높였다.

활동 후보:

- `exp_1`: 교내 커머스 리뉴얼
- `exp_2`: 추천 시스템 개선

선택 결과: `exp_2` 추천 시스템 개선

결과 응답:

> 내용을 분석하여 경험을 정리했어요.
>
> - 담당업무 아래 1개의 블록 생성

커밋 결과:

```json
{
  "previous_version": 1,
  "map_version": 2,
  "applied": [
    {
      "item_id": "file_1",
      "block_id": "311",
      "path": "추천 시스템 개선 > 담당업무"
    }
  ],
  "dropped": []
}
```

블록 생성 결과:

```text
[exp_2] 추천 시스템 개선
  [b_1] 담당업무
    [b_2/311] 사용자 행동 데이터 분석 → 협업 필터링 모델 개선으로 추천 정확도 12% 향상
```

판정: 활동 선택 프롬프트에 파일 텍스트가 포함됐고, 파일 내용과 관련 있는 활동을
선택해 그 아래에 블록을 생성했다.

## 3. 기존 내용은 제외하고 신규 내용만 반영

입력:

> 행사 신청 페이지의 이탈률이 높았다는 내용은 이미 있으니 제외하고, Redis 캐시를
> 적용해 중복 조회를 줄인 내용만 반영해줘.

기존 블록:

```text
[b_1] 문제 해결
  [b_2] 행사 신청 페이지의 이탈률이 높았다.
  [b_3] GA4 퍼널 분석 후 입력 단계를 5개에서 3개로 줄였다.
```

필터 결과:

```json
{
  "new_items": [
    {
      "item_id": "new_1",
      "text": "Redis 캐시를 적용해 중복 조회를 줄인 내용",
      "source": "message"
    }
  ],
  "excluded_reasons": [
    "현재 활동에 이미 작성된 내용"
  ]
}
```

결과 응답:

> 내용을 분석하여 경험을 정리했어요.
>
> - 문제 해결 아래 1개의 블록 생성

블록 생성 결과:

```text
[b_1] 문제 해결
  [b_2] 행사 신청 페이지의 이탈률이 높았다.
  [b_3] GA4 퍼널 분석 후 입력 단계를 5개에서 3개로 줄였다.
  [b_4/402] Redis 캐시 적용 → 중복 조회 감소
```

판정: content filter가 현재 활동 트리를 함께 비교했고, 이미 존재하는 이탈률
내용은 중복 생성하지 않고 Redis 캐시 내용만 생성했다.

## 4. 신규 블록을 후속 질문 anchor로 사용

입력:

> 결제 실패율을 낮추기 위해 Redis 캐시를 적용함

결과 응답:

> 내용을 분석하여 경험을 정리했어요.
>
> - 문제 해결 아래 1개의 블록 생성

후속 응답:

> Redis 캐시를 해결 방법으로 선택한 기준은 무엇이었나요?

블록 생성 결과:

```text
[b_1] 문제 해결
  [b_4/402] 결제 실패율을 낮추기 위해 Redis 캐시를 적용함
```

저장된 gap:

```json
{
  "gap_type": "extend_block",
  "anchor_block_id": "402",
  "message": "Redis 캐시를 해결 방법으로 선택한 기준은 무엇이었나요?"
}
```

판정: gap 분석 시 사용한 임시 `item_id`가 커밋 이후 실제 생성된 블록 ID
`402`로 치환됐다. 후속 답변은 다음 요청에서 정확한 블록을 수정할 수 있다.

## 5. gap 분석에 실패해도 커밋 유지

입력:

> 슬로우 쿼리 알림을 추가해 재발을 방지함

gap 분석 단계에서 의도적으로 `LlmError`를 발생시켰다.

결과 응답:

> 내용을 분석하여 경험을 정리했어요.
>
> - 문제 해결 아래 1개의 블록 생성

대체 후속 응답:

> 더 정리하고 싶으신 내용이 있나요?

블록 생성 결과:

```text
[b_1] 문제 해결
  [b_4/402] 슬로우 쿼리 알림을 추가해 재발을 방지함
```

상태 결과:

```json
{
  "map_version": 2,
  "committed_block_id": "402",
  "saved_active_gap": null
}
```

판정: gap 분석 실패가 이미 성공한 커밋을 취소하지 않았다. 고정 후속 문구를
반환했고, 이전 요청에서 남아 있던 활성 gap도 제거했다.

## 6. 활성 gap 답변으로 기존 블록 수정

활성 gap이 기존 블록 `301`을 가리키는 상태에서 다음 답변을 입력했다.

입력:

> GA4로 이탈 구간을 확인했고 입력 단계를 5개에서 3개로 줄였어요.

수정 전:

```text
[301] 행사 신청 페이지의 이탈률이 높았다.
```

결과 응답:

> 내용을 분석하여 경험을 정리했어요.
>
> - 문제 해결 아래 1개의 블록 수정

커밋 결과:

```json
{
  "previous_version": 1,
  "map_version": 2,
  "applied": [
    {
      "item_id": "gap_update_1",
      "block_id": "301",
      "path": "교내 커머스 리뉴얼 > 문제 해결"
    }
  ],
  "dropped": []
}
```

수정 후:

```text
[301] 행사 신청 페이지의 이탈률이 높았고, GA4로 이탈 구간을 확인해 입력 단계를 5개에서 3개로 축소함
```

판정: 새 블록을 중복 생성하지 않고 활성 gap의 anchor 블록을 수정했다.

## 7. 검증 보정 한도 초과 시 부분 커밋 방지

정상 항목과 잘못된 부모 참조를 가진 항목을 함께 검증에 전달했다.

입력 operation:

```json
[
  {
    "item_id": "good",
    "action": "add",
    "parent_ref": "b_1",
    "text": "정상"
  },
  {
    "item_id": "bad",
    "action": "add",
    "parent_ref": "exp_999",
    "text": "오류"
  }
]
```

오류 응답:

```json
{
  "code": "validation_failed",
  "failed_node": "validate",
  "retryable": true,
  "message": "정리 결과 검증에 실패했습니다."
}
```

맵 버전:

```text
검증 전: 1
검증 후: 1
```

판정: 보정 한도를 넘긴 뒤 `ValidationError`를 반환했으며 정상 항목까지 포함해
어떤 operation도 부분 커밋하지 않았다.

## 8. 테스트 콘솔 스캔 PDF 전체 경로

테스트 일시: 2026-08-29

입력:

- 3페이지 한글 스캔 PDF (내장 텍스트 레이어 없음)
- 사용자 메시지: `첨부한 프로젝트 경험을 경험 맵 블록으로 정리해줘.`
- 파일 처리 모델: `google/gemini-3.1-flash-lite`
- 경험정리 모델: `openai/gpt-4.1-mini`

실제 테스트 콘솔의 multipart/SSE API로 업로드했다. 처리 경로는 다음 노드를 모두
통과했다.

```text
router → file_processor(OCR 3페이지) → file_cleanup → content_filter
→ target_activity → structure → refine → validate → commit
```

결과 응답:

> 내용을 분석하여 경험을 정리했어요.
>
> - 문제해결 아래 5개의 블록 생성
> - 문제해결 생성

커밋 결과 요약:

```json
{
  "previous_version": 1,
  "map_version": 2,
  "applied_count": 6,
  "dropped": []
}
```

블록 생성 결과:

```text
[exp_1] 새 경험
  [b_1] (문제해결 카테고리 컨테이너)
    [b_2] (문제해결 요약 빈 슬롯)
      [b_3] 행사 신청 과정에서 사용자의 이탈률이 높았습니다.
      [b_4] GA4 퍼널 분석을 통해 입력 단계를 5개에서 3개로 줄이고,
            Redis 캐시를 적용해 중복 조회를 감소시켰습니다.
      [b_5] 프로젝트: 교내 커머스 리뉴얼
      [b_6] 신청 완료율이 18% 증가하고 평균 응답 시간이 40% 감소하는
            성과를 거두었습니다.
```

후속 gap 응답:

> GA4 퍼널 분석과 Redis 캐시 적용은 구체적으로 어떻게 진행하셨나요?

판정: PDF OCR 성공만 확인한 것이 아니라 테스트 콘솔과 동일한 실제 API·LLM·SSE
경로에서 블록 커밋과 후속 gap 생성까지 완료했다. 이 과정에서 확인된 구조화 모델의
부모 참조 중복, 비공식 슬롯 이름, 일반·트러블슈팅 템플릿 중복 선택, 이전 배치
item을 기존 맵 별칭으로 오인하는 경우는 원문이나 배정 사실을 추측하지 않는
결정적 보정으로 처리했다.

### 2026-08-30 실패 재현 후 회귀 실행

사용자 환경에서 재시작 후에도 실패한 요청과 같은 조건으로 스캔 PDF를 다시
실행했다. 첫 구조화 이후 검증 보정으로 구조화를 다시 수행할 때, 모델이
`활동 → 카테고리 → 앵커 → level 5 슬롯` 계층을 건너뛰거나 다른 section의
앵커 아래에 슬롯을 붙이는 문제를 재현했다. slot_id와 카탈로그의 앵커 정의로
부모가 확정되는 신규 item만 결정적으로 재연결하도록 수정한 뒤 실제 API를 다시
호출했다.

최종 요청:

```text
request_id: 8c1f5c30-82ba-4f7c-ad96-f97508347744
router → file_processor → file_cleanup → content_filter → target_activity
→ structure → refine → validate → commit → processing_complete
```

실제 응답:

> 내용을 분석하여 경험을 정리했어요.
>
> - 담당업무 아래 5개의 블록 생성
> - 담당업무 생성

실제 커밋 결과:

```json
{
  "previous_version": 1,
  "map_version": 2,
  "applied_count": 6,
  "dropped": []
}
```

실제 블록 생성 결과:

```text
[exp_1] 새 경험
  [b_1] (담당업무 카테고리 컨테이너)
    [b_2] 프로젝트: 교내 커머스 리뉴얼
      [b_3] 행사 신청 과정에서 사용자의 이탈률이 높았습니다.
      [b_4] GA4 퍼널 분석을 통해 입력 단계를 5개에서 3개로 줄이고,
            Redis 캐시를 적용해 중복 조회를 감소시켰습니다.
      [b_5] 신청 완료율이 18% 증가하고 평균 응답 시간이 40% 감소하는
            성과를 거두었습니다.
      [b_6] (빈 조사·학습 슬롯)
```

판정: 내장 텍스트가 없는 3페이지 PDF의 OCR부터 실제 블록 커밋까지 성공했다.
최종 맵은 신규 카테고리와 앵커를 포함해 명세의 부모 계층을 지켰고, 중간
level 5 슬롯 중첩이나 다른 section 앵커 연결은 남지 않았다.

### PDF 전 페이지 OCR 정책 검증

사용자 결정에 따라 PDF 텍스트 레이어 직접 추출을 제거하고, 모든 PDF 페이지를
PNG로 렌더링한 뒤 OCR 모델로 읽도록 변경했다. 텍스트 레이어가 포함된 실제 파일
`sample_experience copy.pdf`로 테스트 콘솔 전체 경로를 실행했다.

```text
PDF 전체 페이지 OCR 준비 완료 (pages=1)
PDF 1페이지 OCR 시작 (image_bytes=332152)
PDF 1페이지 OCR 완료 (text_chars=912)
content_filter: 새 내용 16개
```

첫 재현에서는 모델이 카탈로그에 없는 `TASK.BASIC.LEARNING` 슬롯을 만들었고,
검증 보정 단계에서는 아직 커밋되지 않은 `b_1`을 기존 블록 별칭으로 오인했다.
TASK 기본 템플릿의 배운 점은 공식 `TASK.BASIC.RESULT`로 병합하고, 현재 맵에 없는
부모 별칭은 선택 활동 아래의 올바른 section·anchor로 재배치하도록 보정했다.

최종 요청:

```text
request_id: 77d549e6-caf9-462b-8940-e7f63f0a6c1f
router → file_processor(전 페이지 OCR) → file_cleanup → content_filter
→ target_activity → structure → refine → validate → structure → refine
→ validate → commit → processing_complete
```

최종 결과:

```json
{
  "previous_version": 1,
  "map_version": 2,
  "applied_count": 6,
  "dropped": [],
  "status": "completed"
}
```

판정: 텍스트 레이어가 있는 PDF도 OCR 모델만 사용했고, OCR 결과의 구조화 보정과
블록 커밋 및 후속 질문 생성까지 테스트 콘솔에서 완료했다.

### 파일 전용 요청의 구조 검증 재호출 제거

동일 PDF를 사용자 메시지 없이 파일만 첨부했을 때, 모델이 카탈로그에 없는
`PROBLEM_SOLVING.RECOVERY.LEARNING` 슬롯을 만들고 자동 보정으로 생성된 부모가
자식 뒤에 놓여 validate가 구조화를 반복하는 문제를 재현했다.

- 카탈로그에 없는 `LEARNING`·`LESSON` 계열 슬롯은 실제로 배운 점을 받는
  `TASK.BASIC.RESULT`로 귀속
- 자동 생성한 operation은 카테고리 → 앵커 → 자식 순으로 안정적 위상 정렬
- 순환·없는 부모는 임의 수정하지 않고 기존 검증에서 거부

최종 실제 요청:

```text
request_id: eed549e6-caf9-462b-8940-e7f63f0a6c1f
입력: user_message 없음, sample_experience copy.pdf 1개
router → file_processor(전 페이지 OCR) → file_cleanup → content_filter
→ target_activity → structure → refine → validate → commit
```

```json
{
  "previous_version": 1,
  "map_version": 2,
  "applied_count": 12,
  "dropped": [],
  "status": "completed"
}
```

판정: validate 이후 structure 재호출 없이 첫 검증에서 바로 커밋됐다. 담당업무와
문제해결 카테고리 및 하위 블록이 부모 우선 순서로 생성됐고 후속 질문도 정상
반환됐다.

### 파일 구획 문맥이 배치 사이에서 유실되는 문제

Tailscale로 연 테스트 콘솔에서 동일 PDF를 다시 처리한 요청은 완료됐지만,
최종 트리의 의미 배정에 문제가 있었다.

```text
request_id: 506c2240-db0c-46f7-905a-80f877dce4a3
status: completed
OCR: 912자
원문 item: 24개
생성 블록: 12개
dropped: 0개
```

- `상황` 제목이 내용 블록 `상황 설명`으로 생성됨
- Kafka 비동기 전환·커넥션 풀·서킷 브레이커 내용이 문제해결 해결책이 아닌
  담당업무 아래로 배정됨
- 문제해결 해결책 블록은 빈 가이드 문구로 남음
- 명시적인 `주요 성과`·`배운 점` 구획이 별도 카테고리로 분류되지 않음

원인은 파일 item을 구조화 LLM에 한 개씩 보내는 배치 정책에서, 해당 item
앞의 `상황`·`원인 분석`·`해결 과정`·`결과` 제목이 함께 전달되지 않은
것이다. 다음과 같이 보정했다.

- content filter에서 단독 문서 제목을 결정적으로 제외
- 각 파일 item의 원본 위치를 찾아 가장 가까운 앞쪽 구획을 slot 힌트로 전달
- 제목으로 확정된 slot은 LLM 출력과 다르더라도 코드가 보정
- slot이 다른 section으로 바뀌면 level 3 카테고리 부모도 함께 재구성
- 비공식 learning 슬롯은 카탈로그에 `LEARNING.GROWTH`가 있으면 그곳으로 정규화

실제 OpenRouter 재실행은 동일 PDF의 추가 외부 전송 승인 후 확인한다.

사용자가 수정된 Tailscale 테스트 콘솔에서 동일 PDF를 직접 재실행했다.

```text
request_id: 6ba676de-30f2-420a-b38d-0a71ce69b634
status: completed
OCR: 912자
원문 item: 15개
생성 블록: 17개
dropped: 0개
```

핵심 보정은 실제로 적용됐다.

- Kafka 비동기 전환·커넥션 풀 재산정·서킷 브레이커가 문제해결 `SOLUTION`으로 배정
- P99 320ms·92% 개선·3개월 미재발이 `VERIFICATION`으로 배정
- 45분→12분, 58%→82%가 `ACHIEVEMENT.QUANTITATIVE`로 배정
- 구조적 원인 분리에 대한 학습이 `LEARNING.GROWTH`로 배정

다만 문제해결 제목 전체를 content filter 모델이 제외해 SUMMARY가
비었고, 담당업무 내용이 모두 SUMMARY로 옮겨진 뒤에도 빈 `TASK.BASIC.*`
템플릿 4개가 남았다. 후속 보정으로 다음을 추가했다.

- `문제 해결 경험 — <구체 요약>` 형식의 제목을 모델이 제외해도
  구분자 뒤의 원문 부분을 SUMMARY item으로 복구
- 하위 템플릿의 모든 슬롯이 비었으면 템플릿 전체를 생성 대상에서 제외

## 재현 명령과 회귀 테스트

1번 전체 그래프 데모:

```bash
UV_CACHE_DIR=/tmp/folioo-uv uv run python scripts/experience_map/demo.py
```

2~7번은 각 노드와 coordinator에 고정 LLM 출력을 주입한 일회성 통합 드라이버로
실행했다. 같은 처리 경로의 영구 회귀 테스트는 다음 명령으로 재현할 수 있다.

관련 회귀 테스트:

```bash
UV_CACHE_DIR=/tmp/folioo-uv uv run pytest -q \
  tests/test_features/test_experience_map/test_input_scenarios.py \
  tests/test_features/test_experience_map/test_coordinator.py \
  tests/test_features/test_experience_map/test_gap_analysis.py \
  tests/test_features/test_experience_map/test_target_activity.py \
  tests/test_features/test_experience_map/test_content_filter.py \
  tests/test_features/test_experience_map/test_validate.py \
  tests/test_features/test_experience_map/test_test_runtime.py
```

최신 경험정리 전체 테스트 실행 결과:

```text
404 passed, 51 skipped
```

## 남은 실연동 확인 항목

실제 OpenRouter 모델과 테스트 콘솔의 multipart/SSE 소비 경로까지 확인했다. 테스트
콘솔의 커밋 대상은 메모리 맵이므로, 배포 전에는 메인 백엔드 commit/revert API와
실제 GCS 임시 파일 저장소를 연결한 통합 테스트가 별도로 필요하다.
