---
id: "3.09"
phase: 3
title: "티켓 검증 미들웨어와 CORS·rate limit"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.02"]
blocks: ["3.10"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.09 — 티켓 검증 미들웨어와 CORS·rate limit

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 9절 8번
> API: [`docs/architecture/experience-map-api-spec.md`](../../docs/architecture/experience-map-api-spec.md)
> PR: EM-09 · 브랜치 `feat/{issue}-experience-map-ticket-auth`
> GitHub Issue: [#306](https://github.com/Teamie71/folioo-ai/issues/306)

## 의존성

- 3.02 (스키마·오류 모델) — 티켓 오류 응답 포맷을 사용한다.

## 사전 준비

- [ ] API 명세의 티켓 구조(`sub`·`sid`·만료·서명 방식) 확인
- [ ] 기존 `app/middleware/auth.py` 패턴 확인
- [ ] 웹 오리진 목록 확보

## 구현 체크리스트

- [ ] 티켓 검증 미들웨어 — **서명 → 만료 → `sid` == path `session_id`** 순서
- [ ] **요청 body 를 읽기 전에** 검증 수행
- [ ] CORS 에 웹 오리진과 `Authorization` preflight 추가
- [ ] 티켓 `sub` 단위 rate limit
- [ ] 실패 케이스별 오류 코드 분리

## Definition of Done

- [ ] 위조·만료 티켓과 `sid` 불일치가 각각 차단되고 오류 코드가 구분된다
- [ ] body 를 읽지 않고 거부되는 것이 테스트로 확인된다
- [ ] 서명 검증에 상수 시간 비교를 사용한다
- [ ] rate limit 초과 시 429 와 재시도 안내가 나간다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- 보안 경계라 3.10(API·SSE 뼈대)에서 분리했다. 리뷰어는 이 PR 만 집중해서 본다.
- 티켓은 세션 단위 권한이다. `sid` 검증이 빠지면 다른 사용자 세션에 접근할 수 있다.
