---
id: "3.23"
phase: 3
title: "시나리오 테스트와 운영 검증, feature flag 전환"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.12", "3.14", "3.20", "3.21", "3.22"]
blocks: []
estimate: "L"
status: "todo"
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

## 구현 체크리스트 — 시나리오 테스트 14종

- [ ] 파일(파서) → 새 block 추가
- [ ] 파일(OCR) → 새 block 추가
- [ ] 채팅 → 새 block 추가
- [ ] gap 답변 → refine 분기 (기존 블록 결합)
- [ ] gap 답변 → structure 분기 (하위 블록 생성)
- [ ] gap 답변 + 새 내용 동시 입력
- [ ] 없는 3단계 카테고리 생성
- [ ] 담당업무 템플릿으로 4·5단계 생성
- [ ] 문제해결 템플릿 6종 중 선택하여 5단계 생성
- [ ] 기능 밖 fallback
- [ ] 추출 불가 파일 → fallback
- [ ] 노드 실패 → 사용자 재시도
- [ ] gap 분석 실패 → 결과만 응답
- [ ] SSE 단절 → request 결과 조회

## 구현 체크리스트 — DB·연동 통합 테스트

- [ ] session 생성 경쟁, running request 경쟁
- [ ] 커밋 API 멱등 (같은 `request_id` 재호출)
- [ ] `409 map_version_conflict` 1회 복구와 최종 실패
- [ ] 커밋 성공 후 응답 유실 → `GET /commit/{request_id}` 복구
- [ ] `422 unknown_slot_id` → 카탈로그 재조회 후 재시도
- [ ] revert 성공·충돌·만료 (메인 서버 연동 확인)

## Definition of Done

- [ ] 시나리오 14종이 모두 통과한다
- [ ] DB·연동 통합 테스트가 모두 통과한다
- [ ] 단위 테스트 10종(9절 22번)이 모두 존재한다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 전부 통과
- [ ] **`EXPERIENCE_MAP_ENABLED` 기본값을 `true` 로 전환**
- [ ] 남은 결정 사항(10절)이 모두 해소됐거나 잔여 항목이 문서에 기록됐다

## 리스크 / 메모

- feature flag 전환은 이 태스크의 **마지막 단계**다. 시나리오가 다 통과하기 전에 뒤집지 않는다.
- 되돌리기(9절 21번)는 메인 서버 구현이므로 AI 작업은 없지만, 연동 확인은 여기서 함께 한다.
