---
id: "3.03"
phase: 3
title: "템플릿 카탈로그 클라이언트와 TTL 캐시"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.02"]
blocks: ["3.06", "3.15"]
estimate: "S"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.03 — 템플릿 카탈로그 클라이언트와 TTL 캐시

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 3절, 9절 3번
> PR: EM-03 · 브랜치 `feat/{issue}-experience-map-template-catalog`

## 의존성

- 3.02 (스키마·오류 모델) — 카탈로그 응답 모델과 오류 타입을 사용한다.
- **외부-E**: 메인 서버 `GET /templates`. 미구현이어도 API 명세 기준 계약 테스트로 선행한다.

## 사전 준비

- [ ] API 명세의 `GET /templates` 응답 형태 확인
- [ ] `common/http_client/`, `common/main_server/` 기존 클라이언트 패턴 확인

## 구현 체크리스트

- [ ] `features/experience_map/templates.py` — 카탈로그 조회
- [ ] 기동 시 1회 조회 + 1시간 TTL 갱신
- [ ] 동시 요청이 카탈로그를 중복 조회하지 않도록 single-flight 처리
- [ ] 강제 재조회 훅 노출 (`unknown_slot_id` 대응용, 실제 사용은 3.18)
- [ ] `slot_id` → placeholder·작성 예시 조회 헬퍼 (few-shot 프롬프트용)

## Definition of Done

- [ ] TTL 만료 시 재조회하고, 만료 전에는 재조회하지 않는다
- [ ] 카탈로그 조회 실패가 앱 기동을 막지 않고 첫 사용 시점에 재시도된다
- [ ] 동시 요청 N 건에서 실제 HTTP 호출이 1회다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- 카탈로그 본체 위치(코드 상수 vs DB)는 메인 서버 결정 사항이며 AI 서버 영향이 없다 (통합 문서 10절 3번).
- **AI 는 placeholder 문구를 재생산하지 않는다.** 식별자만 고르고 문구 부여는 메인 서버 몫이다.
