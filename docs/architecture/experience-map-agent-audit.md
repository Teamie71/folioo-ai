# 경험정리 에이전트 구현 적합성 감사 보고서

> 감사 기준: [`experience-map-agent.md`](experience-map-agent.md)
>
> 최초 감사: 2026-08-26 / 수정 후 재검증: 2026-08-27
>
> 범위: AI 서버의 경험정리 에이전트 구현과 관련 단위·API 테스트

## 1. 결론

최초 감사에서는 운영 경로의 충돌 복구, Validation 안전망, 파일 삭제 순서,
Router 최종 실패 정책, async 테스트 종료 문제를 발견해 **부분 충족**으로 판정했다.

수정 후에는 다음을 확인했다.

1. 첫 `409 map_version_conflict`에서 최신 맵을 읽고 Structure 또는 Validation부터
   재실행한 뒤 한 번 더 커밋한다.
2. alias별 위계·부모·편집 권한을 state에 전달하고 Validation에서 독립적으로 검사한다.
3. 파일 추출 state를 sync checkpoint로 저장한 다음 별도 cleanup 노드에서 원본을 삭제한다.
4. Router는 내부에서 정확히 한 번 재시도하고, 두 번 모두 실패하면 Fallback으로 끝낸다.
5. LangGraph 실행 스트림과 테스트용 async callable을 정리해 관련 테스트가 종료된다.

따라서 이 저장소가 담당하는 에이전트 코드와 자동화 테스트 범위의 종합 판정은
**충족**이다. 실제 메인 서버의 템플릿·맵 API, PostgreSQL checkpoint, GCS를 함께 쓰는
배포 환경의 통합 확인은 별도로 필요하다.

## 2. 감사 방법

다음 순서로 문서와 구현을 대조했다.

1. 문서의 공통 규칙, 파이프라인, 노드 명세, 상태·재시도 규칙을 요구사항으로 분해
2. `features/experience_map/`의 그래프, 노드, coordinator, service, repository 대조
3. `app/api/v1/experience_map.py`와 API 스키마의 경계 확인
4. `tests/test_features/test_experience_map/`와 API 테스트의 요구사항 증명 범위 확인
5. Ruff 및 실행 가능한 관련 테스트 수행

판정 기준은 다음과 같다.

| 판정 | 의미 |
| --- | --- |
| 충족 | 구현과 직접 검증하는 테스트가 모두 존재 |
| 부분 충족 | 주요 구현은 있으나 예외 경로·검증·통합 연결이 부족 |
| 미충족 | 요구 동작이 운영 경로에서 실행되지 않거나 반대로 동작 |
| 확인 필요 | 외부 메인 서버·DB·GCS 등 이 저장소만으로 최종 확인 불가 |

## 3. 요구사항 추적표

| 문서 영역 | 판정 | 구현 근거 | 비고 |
| --- | --- | --- | --- |
| 1-2 기능 4가지 | 충족 | `graph.py`, `content_filter.py`, `structure.py`, `refine.py`, `fallback.py` | 파일·채팅·gap·Fallback 경로와 통합 시나리오 검증 |
| 1-3 제공되지 않은 정보 생성 금지 | 부분 충족 | 필터 원문 역추적, 구조화 원문 재조립, 정제 수치·영문 토큰 검증 | 한국어 사실·관계의 hallucination을 결정적으로 검증하지는 못함 |
| 1-3 노드별 자동 재시도 1회 | 충족 | `RetryPolicy(max_attempts=2)`, LLM 내장 retry 0 | gap·커밋에는 RetryPolicy가 적용되지 않음 |
| 2-3 AI 위계별 권한 | 충족 | `map_context.py`, `state.py`, `validate.py` | level 1~3 수정, 편집 불가 블록, level 5 하위 생성을 차단 |
| 3 템플릿·빈 슬롯 전개 | 충족 | `templates.py`, `structure.py`의 슬롯 보완·검증 | 담당업무 1종과 문제해결 6종 테스트가 존재 |
| 4 파이프라인 | 충족 | `graph.py` | 커밋·gap은 문서대로 graph 밖 coordinator에서 실행 |
| 5-1 Router | 충족 | 파일 유무 코드 판정, LLM intent 분류, 2회 실패 Fallback | 그래프 RetryPolicy와 중복되지 않게 노드 내부에서 재시도 |
| 5-2 파일처리 | 충족 | 입력 순서 유지, PDF 텍스트 우선·스캔 페이지만 OCR, 오류 유형 분리, `file_cleanup` | 페이지별 OCR, sync checkpoint 뒤 원본 삭제 |
| 5-3 반영 내용 필터링 | 충족 | 세 분류, 원문 역추적, active gap 방어 | 기존 내용 조건부 조회 Tool은 별도 확인 필요 |
| 5-4 대상 활동 선택 | 충족 | 화면 context, gap anchor, outline 순 선택 | 대상 불명확 시 `ambiguous_target` Fallback |
| 5-5 블록 단위 구조화 | 충족 | 원문 보존, alias 제한, 템플릿 선택·전개 검증 | 모델 오류를 결정적 코드 보정과 검증으로 방어 |
| 5-6 문장 정제 | 부분 충족 | 배정 필드 없는 출력, item 집합 검증, 수치·영문 토큰 방어 | 모든 사실 보존을 일반적으로 판정할 수는 없음 |
| 5-7 Validation | 충족 | `nodes/validate.py`, `map_context.py` | 부모 위계·형제 관계·수정 권한·내용 제약을 독립 검증 |
| 5-8 커밋 | 충족 | alias 역변환, shield, 응답 유실 조회, conflict recovery graph | 첫 충돌 자동 복구, 두 번째 충돌만 최종 실패 |
| 5-9 결과 응답 | 충족 | `nodes/result_response.py` | LLM 없는 결정적 템플릿 사용 |
| 5-10 gap 분석 | 충족 | `nodes/gap_analysis.py`, `suggestion_response.py` | 실패 시 결과 요청을 실패로 바꾸지 않음 |
| 5-11 Fallback | 충족 | 진입 사유별 고정 문구 | DB 변경 없이 completed 저장은 service/repository 경로로 처리 |
| 6 별칭 화이트리스트·한 활동 쓰기 | 충족 | `map_context.py`, `target_activity.py`, `structure.py` | 실제 ID는 LLM 컨텍스트에 렌더링하지 않음 |
| 7 상태와 사용자 재시도 | 충족 | thread namespace, turn 초기화, sync checkpoint, resume | checkpoint runner 테스트 정상 종료 |
| 9-17 결과·gap coordinator | 충족 | `coordinator.py` | 정상·gap 실패·commit 실패 병렬 동작 테스트 존재 |
| 9-20 연결 종료와 복구 | 부분 충족 | lease, commit shield, stale request reconciliation | 실제 네트워크 단절·다중 worker 배포 통합 검증은 확인 필요 |

## 4. 주요 발견 사항

### 4-1. 해결됨 — map version 충돌 복구 운영 연결

수정 후 `graph_runner.py`가 최신 snapshot을 다시 구성하는 callback을 커밋에 전달한다.
첫 충돌이면 coordinator가 기존 gap task를 취소하고 복구 graph를 실행한 뒤, 갱신된 state로
gap 분석과 커밋을 다시 시작한다. 두 번째 충돌은 기존 정책대로 최종 실패한다.

아래는 최초 감사에서 확인한 문제와 수정 기준이다.

문서 5-8은 첫 번째 `409 map_version_conflict`가 발생하면 최신 맵을 다시 읽고,
구조가 유지됐으면 Validation부터, 구조가 바뀌었으면 Structure부터 한 번 재실행하도록
요구한다. 두 번째 충돌에서만 `commit_conflict`로 끝나야 한다.

`nodes/commit.py`에는 최신 맵을 주입받아 `commit_recovery_node`를 계산하는 로직이
있다. 그러나 기본 coordinator는 `commit_changes`를 그대로 task로 실행하며
`refresh_map`을 전달하지 않는다. 이 경우 첫 충돌도 복구하지 못하고
`CommitConflictError`가 된다.

설령 별도 runner가 `refresh_map`을 주입해 복구 상태를 반환하더라도 coordinator는
`commit_recovery_node`를 확인하지 않고 즉시 `commit_result`를 검증한다. 따라서
Structure/Validation 재실행과 두 번째 커밋이 운영 흐름에 연결되어 있지 않다.

영향:

- 사용자가 편집 중이거나 map version이 자주 변경되는 환경에서 복구 가능한 요청도 실패
- 문서가 보장하는 1회 자동 충돌 복구가 동작하지 않음

권장 조치:

1. coordinator 또는 전용 commit orchestration 계층에 최신 snapshot 갱신 callback 주입
2. `commit_recovery_node`가 있으면 해당 노드부터 그래프를 재실행
3. 재검증된 items로 커밋을 한 번 더 실행
4. coordinator를 포함한 `409 → 재실행 → 성공` 및 `409 → 재실행 → 409` 통합 테스트 추가

관련 파일:

- `features/experience_map/coordinator.py`
- `features/experience_map/nodes/commit.py`
- `features/experience_map/graph_runner.py`

### 4-2. 해결됨 — Validation 위계·권한 검증 보강

수정 후 map context가 alias별 `block_id`, 부모 alias, level, kind,
`is_text_editable`을 state에 전달한다. Validation은 기존 블록과 같은 요청의 신규 블록을
함께 계산해 최대 level, level 1~3 수정 금지, 편집 권한, `after_ref` 형제 관계,
slot·section 위계를 검사한다.

아래는 최초 감사에서 확인한 문제와 수정 기준이다.

문서 5-7은 다음을 Validation 노드의 책임으로 명시한다.

- 부모·target·after 존재 및 after가 같은 부모의 형제인지 확인
- 부모와 생성 블록의 위계 및 level 5 초과 금지
- level 1·2 생성 금지, level 3 수정 금지, 삭제 금지
- `is_text_editable` 확인
- 입력 사실 보존과 hallucination 금지

현재 `validate.py`는 주로 다음만 검사한다.

- operation 스키마
- alias 화이트리스트 존재 여부
- 신규 부모가 앞선 item인지 확인
- 선택 활동 자체의 update 차단
- 빈 문자열과 500자 상한
- 정제 전후 item 집합

또한 `MapBlockRow`에는 `level`, `parent_id`, `is_text_editable` 정보가 있지만,
`ActivityContext`와 LangGraph state에는 alias별 메타데이터가 전달되지 않는다.
따라서 현재 Validation 노드는 문서가 요구한 검사를 수행할 정보 자체가 부족하다.

Structure 노드가 생성 operation을 강하게 검증하는 것은 좋은 1차 방어지만,
Validation은 gap update와 향후 다른 operation 경로까지 포함하는 최종 안전망이어야 한다.

영향:

- level 3 또는 편집 불가 블록 update가 AI 서버 Validation을 통과할 수 있음
- `after_ref`가 존재하기만 하면 실제 같은 부모의 형제인지 확인하지 못함
- 잘못된 부모 아래 추가해 level 5를 초과하는 operation을 최종 단계에서 독립적으로 차단하지 못함

권장 조치:

1. alias별 `block_id`, `parent_id`, `level`, `is_text_editable`, `section_kind` 메타데이터 추가
2. 기존 블록과 같은 요청 안 신규 블록을 합친 가상 트리를 만들어 위계 계산
3. 위계·권한·형제 관계를 Validation에서 독립적으로 재검증
4. 문서 5-7의 각 bullet과 일대일로 대응하는 테스트 추가

관련 파일:

- `features/experience_map/nodes/validate.py`
- `features/experience_map/map_context.py`
- `features/experience_map/state.py`
- `tests/test_features/test_experience_map/test_validate.py`

### 4-3. 해결됨 — checkpoint 이후 파일 원본 삭제

수정 후 `file_processor`는 추출 결과만 반환하고 원본을 삭제하지 않는다. LangGraph가
sync durability로 해당 state를 checkpoint한 다음 `file_cleanup` 노드가 성공적으로
추출된 파일만 삭제한다. 재개 시 이미 추출된 파일은 다시 파싱하지 않는다.

아래는 최초 감사에서 확인한 문제와 수정 기준이다.

문서는 추출 결과를 checkpoint에 저장한 뒤 GCS 원본을 삭제하도록 요구한다. 현재
`process_files()`는 추출 결과를 로컬 리스트에 추가한 직후 `delete_object()`를 호출하고,
그 뒤에야 반환 state의 `extracted_files`와 `extracted_text`를 채운다.

LangGraph checkpoint는 일반적으로 노드가 성공적으로 반환한 state update가 반영된 뒤
저장된다. 따라서 원본 삭제 후 노드 반환 또는 checkpoint 완료 전에 프로세스가 종료되면,
추출 결과와 원본을 모두 잃을 수 있다.

영향:

- 장애 시 사용자 재시도가 파일처리 노드부터 재개되지 못할 가능성
- 다른 worker가 GCS 원본을 읽어 복구한다는 설계 보장 훼손

권장 조치:

- 추출과 원본 삭제를 서로 다른 checkpoint 단계로 분리하거나
- checkpoint 완료를 확인한 service/후처리 계층에서 삭제하거나
- 삭제 대상만 state에 기록하고 다음 결정적 cleanup 노드에서 삭제

현재 `test_original_is_deleted_after_extraction`은 삭제 여부만 확인하므로,
checkpoint 이후 삭제 순서와 장애 구간을 검증하는 테스트가 추가로 필요하다.

관련 파일:

- `features/experience_map/nodes/file_processor.py`
- `tests/test_features/test_experience_map/test_file_processor.py`
- `tests/test_features/test_experience_map/test_checkpoint_runner.py`

### 4-4. 해결됨 — Router 재시도 소진 후 Fallback

수정 후 Router가 내부에서 최대 두 번만 분류를 시도하며, 모두 실패하면
`out_of_scope` Fallback state를 반환한다. Router graph 노드의 RetryPolicy는 제거해
중복 재시도를 방지했고, 입력·예외 상세가 로그에 남지 않는 테스트도 추가했다.

아래는 최초 감사에서 확인한 문제와 수정 기준이다.

문서 5-1은 Router LLM 분류가 자동 재시도 후에도 실패하면 Fallback으로 보내도록
명시한다. 현재 Router는 LLM 오류를 `LlmError`로 다시 발생시키고, 그래프의
`RetryPolicy(max_attempts=2)`가 소진되면 service가 요청을 failed로 저장한다.

이 차이는 사용자 경험과 재시도 정책에 직접 영향을 준다.

- 문서 기준: completed, `committed=false`, 재시도 버튼 없음
- 현재 구현: failed, retryable, 사용자 재시도 가능

권장 조치:

- 기획 의도가 Fallback이면 재시도 소진을 식별해 `out_of_scope` 또는 별도
  `classification_failed` Fallback으로 변환
- 시스템 장애를 사용자 재시도 대상으로 유지하려면 문서를 현재 동작에 맞게 수정

관련 파일:

- `features/experience_map/nodes/router.py`
- `features/experience_map/graph.py`
- `features/experience_map/service.py`

### 4-5. 해결됨 — async stream과 테스트 종료 안정화

수정 후 graph runner는 `astream`의 `tasks`·`values` 모드를 사용하고 stream을 명시적으로
닫는다. 그래프에 등록되는 실행 함수와 테스트 `RunnableLambda`도 async callable로 맞췄다.
관련 테스트 424건과 저장소 전체 테스트가 timeout 없이 종료된다.

아래는 최초 감사에서 확인한 문제와 수정 기준이다.

관련 테스트 413개가 정상 수집됐지만 전체 실행은 완료되지 않았다. 다음과 같이
단독 실행에서도 테스트 본문은 `PASSED`가 된 뒤 `pytest-asyncio`의
`asyncio.Runner.close()`에서 대기했다.

- `test_content_filter.py::test_classifies_three_buckets`
- `test_content_filter.py::test_gap_answer_is_kept_when_gap_active`
- `test_checkpoint_runner.py::test_unreadable_file_runs_graph_and_emits_file_fallback_sse`

이는 이번 감사 환경에서 전체 회귀 테스트의 성공 여부를 확정하지 못하게 하는
검증 인프라 문제다. pending task가 남는 원인을 확인하고, 테스트 종료 시 task 누수를
실패로 검출하도록 해야 한다.

권장 조치:

1. 문제 테스트 종료 직전 `asyncio.all_tasks()`로 남은 task 식별
2. LangChain `RunnableLambda`와 LangGraph `astream_events()` 사용 후 background task 정리 확인
3. pytest-asyncio fixture loop scope와 설치 버전 호환성 확인
4. CI에 테스트 전체 실행 timeout을 두어 무한 대기를 명시적 실패로 처리

## 5. 잘 구현된 항목

다음 구현은 문서 의도를 코드 수준에서 비교적 강하게 보장한다.

- 파일 유무를 Router LLM이 아니라 코드로 판정
- Content Filter 출력 조각을 사용자 메시지와 파일 원문에 역추적
- Structure 노드가 LLM이 다시 쓴 문장을 신뢰하지 않고 source item으로 원문 재조립
- 템플릿을 사용할 때 빠진 빈 슬롯을 카탈로그 기준으로 결정적으로 전개
- 문제해결 템플릿 여러 개를 동시에 생성하는 오류 방어
- Refine 출력 스키마에서 배정 관련 필드를 제거
- 정제 전후 item 집합과 새 수치·영문 토큰 생성 여부 확인
- 선택 활동에 한정된 alias 화이트리스트 사용
- 커밋과 gap 분석을 graph 밖에서 병렬 실행해 느린 gap이 결과 응답을 막지 않음
- Fallback과 결과 응답에서 LLM을 사용하지 않고 고정 템플릿 사용
- gap 분석 실패가 성공한 커밋을 failed로 바꾸지 않음
- 요청 lease, 멱등성, 응답 유실 후 commit 조회 복구 경로 구현

## 6. 테스트 실행 결과

### 수정 후 성공

```text
ruff check .
All checks passed!

ruff format --check .
284 files already formatted

pytest tests/test_features/test_experience_map \
  tests/test_app/test_api/test_v1/test_experience_map_api.py -q
424 passed, 1 warning
```

저장소 전체 테스트 최초 실행에서 정적 데모 state의 alias metadata 누락 2건을 발견해
보완했다. 수정 후 전체 suite 결과는 다음과 같다.

```text
pytest -q
1455 passed, 1 warning in 19.64s
```

warning 1건은 다른 HMAC 알고리즘을 거부하는 보안 테스트에서 의도적으로 짧은 HS512
키를 사용하는 과정에 PyJWT가 출력한 경고이며 테스트 실패는 아니다.

## 7. 적용한 수정 순서

1. map version 충돌 복구를 coordinator 운영 흐름에 연결
2. alias block metadata를 state에 추가하고 Validation 요구사항 보강
3. 파일 원본 삭제를 checkpoint 이후 cleanup 단계로 이동
4. Router 최종 실패 정책을 문서와 동일한 Fallback으로 통일
5. async graph stream과 테스트 callable 종료 경로 정리
6. 관련 범위와 저장소 전체 회귀 테스트 실행

## 8. 완료 조건

저장소 코드 범위에서는 다음 조건을 충족했다.

- Validation이 블록 위계·권한·형제 관계·내용 제약을 검사한다.
- 첫 map version 충돌은 자동 복구되고 두 번째 충돌만 최종 실패한다.
- 파일 원본은 추출 결과 checkpoint 이후 삭제된다.
- Router 재시도 소진 정책이 문서와 구현에서 동일하다.
- 관련 테스트 전체가 timeout 없이 종료된다.
- `ruff check .`, `ruff format --check .`, 관련 범위 `pytest`가 통과한다.

운영 배포 전에는 실제 메인 서버와 GCS를 연결한 상태에서 `409 → 복구 → 성공`,
파일 처리 중 worker 종료, SSE 연결 단절·재접속 시나리오를 추가로 확인하는 것이 좋다.
