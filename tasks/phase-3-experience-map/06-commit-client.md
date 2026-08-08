---
id: "3.06"
phase: 3
title: "메인 서버 커밋 API 클라이언트"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.02", "3.03"]
blocks: ["3.18"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.06 — 메인 서버 커밋 API 클라이언트

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 5-8, 9절 5번
> API: [`docs/architecture/experience-map-api-spec.md`](../../docs/architecture/experience-map-api-spec.md) 4-3, 7절
> PR: EM-06 · 브랜치 `feat/{issue}-experience-map-commit-client`
> GitHub Issue: [#305](https://github.com/Teamie71/folioo-ai/issues/305)

## 의존성

- 3.02 (스키마·오류 모델), 3.03 (템플릿 카탈로그 — `unknown_slot_id` 재조회에 사용)
- **외부-B**: 메인 서버 `POST /commit`, `GET /commit/{request_id}`. 미구현이어도 mock 서버 계약 테스트로 선행한다.

## 사전 준비

- [ ] API 명세 4-3 커밋 위임·충돌 복구 흐름 확인
- [ ] `X-API-Key` 인증 방식과 공통 오류 포맷 확인

## 구현 체크리스트

- [ ] `main_client.py` — `POST /commit` 에 `user_id`·`request_id`·`base_map_version`·items 전달
- [ ] `409 map_version_conflict` 를 타입 있는 예외로 승격 (재실행 판단은 3.18)
- [ ] `422 unknown_slot_id` → 카탈로그 재조회 후 **1회 재시도** (클라이언트 내부 완결)
- [ ] `GET /commit/{request_id}` 복구 조회
- [ ] **커밋에는 `RetryPolicy` 를 적용하지 않는다**

## Definition of Done

- [ ] version 충돌을 일반 오류와 구분한다
- [ ] 같은 `request_id` 재호출 시 기존 commit 결과를 반환한다 (메인 멱등성 확인)
- [ ] 커밋 응답 유실 시 `GET /commit/{request_id}` 로 복구된다
- [ ] `422` 재시도가 2회 이상 반복되지 않는다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **커밋은 DB 쓰기가 아니라 메인 서버 API 호출이다.** 위계 검증·`level`·`position`·`kind` 계산·`placeholder` 부여·`ai_commit_log` 는 전부 메인 서버 몫이며 AI 서버에 두지 않는다.
- HTTP 계층만 담당한다. graph 재실행 판단을 여기에 넣으면 3.18 과 책임이 겹친다.
