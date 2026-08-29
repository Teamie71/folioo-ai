# 경험 정리 에이전트 시나리오 테스트 결과

- 실행일: 2026-08-29
- 기준 브랜치: `fix/structure-template-decomposition`
- 기준 커밋: `c308956 fix: align experience map agent with architecture`
- 결과: 7개 시나리오 통과

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
392 passed, 51 skipped
```

## 남은 실연동 확인 항목

실제 OpenRouter 모델과 테스트 콘솔의 multipart/SSE 소비 경로까지 확인했다. 테스트
콘솔의 커밋 대상은 메모리 맵이므로, 배포 전에는 메인 백엔드 commit/revert API와
실제 GCS 임시 파일 저장소를 연결한 통합 테스트가 별도로 필요하다.
