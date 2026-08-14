---
id: "3.23"
phase: 3
title: "시나리오 테스트와 운영 검증, feature flag 전환"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.12", "3.14", "3.20", "3.21", "3.22"]
blocks: []
estimate: "L"
status: "in_progress"
owner: ""
sprint: ""
---

# Task 3.23 — 시나리오 테스트와 운영 검증, feature flag 전환

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 9절 22번
> PR: EM-23 · 브랜치 `test/{issue}-experience-map-scenarios`
> GitHub Issue: [#310](https://github.com/Teamie71/folioo-ai/issues/310)

## 의존성

- 3.12·3.14·3.20·3.21·3.22 — Phase 3 전 구간. 사실상 모든 선행 태스크가 끝나야 한다.

## 사전 준비

- [ ] 각 태스크에서 남긴 미해결 항목 취합
- [ ] 통합 테스트용 DB·mock 메인 서버 환경 준비
- [ ] `slot_id` 목록이 확정됐으면 fixture 카탈로그를 실제 값으로 교체 (3.15)

## 진행 현황 (2026-08-14)

내부 구현의 단위·계약 검증은 선행 태스크에서 갖췄고, 이 태스크에서는 실제
graph 실행 경로를 시나리오 단위로 묶어 검증한다. 다음 항목은 이미 자동화되어
있다.

| 범위 | 근거 테스트 | 상태 |
| --- | --- | --- |
| 기능 밖 fallback | `test_checkpoint_runner.py::test_out_of_scope_input_runs_graph_and_emits_fallback_sse` | 통과 |
| 추출 불가 파일 fallback | `test_checkpoint_runner.py::test_unreadable_file_runs_graph_and_emits_file_fallback_sse` | 통과 |
| 파일(파서) → 새 block 추가 | `test_input_scenarios.py::test_input_kind_reaches_new_block_commit_candidate[parser_file-*]` | 통과 |
| 파일(OCR) → 새 block 추가 | `test_input_scenarios.py::test_input_kind_reaches_new_block_commit_candidate[ocr_file-*]` | 통과 |
| 채팅 → 새 block 추가 | `test_input_scenarios.py::test_input_kind_reaches_new_block_commit_candidate[chat-*]` | 통과 |
| 없는 3단계 카테고리 생성 | `test_structure.py::test_new_category_expands_all_section_slots_and_preserves_source` | 통과 |
| 담당업무 템플릿으로 4·5단계 생성 | `test_structure.py::test_template_expands_empty_slots` | 통과 |
| 문제해결 템플릿 6종 중 선택 | `test_structure.py::test_problem_solving_templates_accept_all_required_slots` | 6종 통과 |
| gap 답변 → 기존 블록 결합 | `test_checkpoint_runner.py::test_gap_answer_uses_expected_graph_path_before_commit[extend_block-*]` | 통과 |
| gap 답변 → 하위 블록 생성 | `test_checkpoint_runner.py::test_gap_answer_uses_expected_graph_path_before_commit[new_child_block-*]` | 통과 |
| gap 답변 + 새 내용 동시 입력 | `test_checkpoint_runner.py::test_gap_answer_uses_expected_graph_path_before_commit[extend_block-True-*]` | 통과 |
| gap 분석 실패 시 결과만 응답 | `test_coordinator.py::test_gap_failure_does_not_fail_committed_result` | 통과 |
| SSE 단절 뒤 결과 조회 | `test_experience_map_api.py::test_request_state_recovers_stored_result` | DB 환경에서 통과 |
| 사용자 재시도·lease 상실 | `test_service.py`의 retry·lease 회귀 테스트 | DB 환경에서 통과 |

아래 항목은 메인 서버 계약 구현·연동 환경이 준비되어야 최종 완료로 표시한다.

- 메인 서버 `block`·`block_kind` 실스키마와 읽기 전용 권한으로 snapshot 조회
- `POST/GET /commit`의 멱등·409 복구·422 카탈로그 재조회
- 되돌리기 성공·충돌·만료

따라서 `EXPERIENCE_MAP_ENABLED` 기본값은 이 문서의 모든 시나리오 및 연동 검증이
끝나기 전까지 `false`로 유지한다.

## 구현 체크리스트 — 시나리오 테스트 14종

- [x] 파일(파서) → 새 block 추가
- [x] 파일(OCR) → 새 block 추가
- [x] 채팅 → 새 block 추가
- [x] gap 답변 → refine 분기 (기존 블록 결합)
- [x] gap 답변 → structure 분기 (하위 블록 생성)
- [x] gap 답변 + 새 내용 동시 입력
- [x] 없는 3단계 카테고리 생성
- [x] 담당업무 템플릿으로 4·5단계 생성
- [x] 문제해결 템플릿 6종 중 선택하여 5단계 생성
- [x] 기능 밖 fallback
- [x] 추출 불가 파일 → fallback
- [x] 노드 실패 → 사용자 재시도
- [x] gap 분석 실패 → 결과만 응답
- [x] SSE 단절 → request 결과 조회

## 구현 체크리스트 — DB·연동 통합 테스트

- [ ] session 생성 경쟁, running request 경쟁
- [ ] 커밋 API 멱등 (같은 `request_id` 재호출)
- [ ] `409 map_version_conflict` 1회 복구와 최종 실패
- [ ] 커밋 성공 후 응답 유실 → `GET /commit/{request_id}` 복구
- [ ] `422 unknown_slot_id` → 카탈로그 재조회 후 재시도
- [ ] revert 성공·충돌·만료 (메인 서버 연동 확인)

## 최종 `dev` 머지

Phase 3 는 `feat/experience-map` 통합 브랜치에 모았다가 **마지막에 한 번에 `dev` 로
머지한다** (3.01 만 배포 환경변수 문제로 dev 에 직접 넣었다).

### 이슈 7개를 이 PR 본문으로 닫는다

```markdown
- Closes #301
- Closes #305
- Closes #306
- Closes #307
- Closes #308
- Closes #309
- Closes #310
```

**개별 PR 의 `Closes` 는 발동하지 않았다.** GitHub 는 PR 이 **기본 브랜치(`dev`)로
머지될 때만** 이슈를 닫는다. Phase 3 의 PR 은 전부 통합 브랜치를 향했으므로 링크만
걸리고 닫히지 않았다. 커밋 메시지에도 닫기 키워드가 없다(확인함). **이 최종 PR 본문이
유일한 자동 닫기 경로다.**

`Closes #301, #305` 처럼 쉼표로 묶으면 **첫 번째만** 인식된다. 각 줄에 `Closes` 를 붙인다.

## Definition of Done

- [ ] 시나리오 14종이 모두 통과한다
- [ ] DB·연동 통합 테스트가 모두 통과한다
- [ ] 단위 테스트 10종(9절 22번)이 모두 존재한다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 전부 통과
- [ ] **`EXPERIENCE_MAP_ENABLED` 기본값을 `true` 로 전환**
- [ ] 남은 결정 사항(10절)이 모두 해소됐거나 잔여 항목이 문서에 기록됐다
- [ ] **최종 `dev` 머지 PR 본문에 `Closes` 7줄을 넣었다** (위 참고)
- [ ] 머지 후 이슈 #301·#305~#310 이 실제로 닫혔는지 확인했다

## 리스크 / 메모

- feature flag 전환은 이 태스크의 **마지막 단계**다. 시나리오가 다 통과하기 전에 뒤집지 않는다.
- 되돌리기(9절 21번)는 메인 서버 구현이므로 AI 작업은 없지만, 연동 확인은 여기서 함께 한다.
- **API 명세는 메인 서버 팀과의 계약 문서인데 최종 머지까지 통합 브랜치에만 있다.**
  그쪽이 지금 구현해야 하는 변경(`GET /templates` 응답 구조 등)이 있으므로, 필요하면
  브랜치 링크로 먼저 공유한다.
