# 경험정리 에이전트 진행 상황

> **기준일**: 2026-08-15
>
> 설계: [에이전트 통합 문서](experience-map-agent.md) ·
> [API 명세](experience-map-api-spec.md) ·
> [PR 분할 계획](experience-map-pr-plan.md) ·
> [운영 검증 태스크](../../tasks/phase-3-experience-map/23-scenario-tests-operational-readiness.md)

## 한눈에 보기

Phase 3의 AI 서버 내부 구현과 시나리오 자동화는 통합 브랜치
`feat/experience-map`에 반영됐다. 최종 완료를 막는 것은 메인 서버가 소유한
스키마·API 계약의 실제 연동 검증이다.

| 구분 | 상태 |
| --- | --- |
| AI 서버 구현·그래프·SSE | 완료 |
| 입력·템플릿 시나리오 14종 | 자동화 완료 |
| PostgreSQL repository·service 통합 테스트 | 완료 |
| 로컬 Swagger·Tailscale 실행 검증 | 완료 |
| 메인 서버 커밋·되돌리기 실연동 | 대기 |
| feature flag 기본값 전환·`dev` 머지 | 대기 |

## 최근 반영

- #343: 만료 lease 요청을 새 `owner_token`으로 원자 claim한 뒤 커밋 결과를 복구한다.
- #344: 파일 파서·OCR·채팅 입력과 템플릿 6종의 graph 시나리오를 검증한다.
- #345: 메인 서버 없이 graph 이벤트를 확인하는 Swagger 데모 API를 추가한다.

## 검증 근거

| 범위 | 결과 |
| --- | --- |
| 경험정리 feature·graph·API 테스트 | `376 passed, 1 warning` |
| 복구 repository·service DB 회귀 | `50 passed` |
| 입력·템플릿 시나리오 | `19 passed` |
| Swagger 데모 API·CLI | `3 passed, 18 skipped` |
| Tailscale 실제 SSE 호출 | `processing_started`부터 `processing_complete`까지 확인 |

로컬 PostgreSQL 컨테이너를 사용해 `CHECKPOINT_DATABASE_URL`과 경험정리
`DATABASE_URL` 연결을 확인했다. `http://100.87.220.124:8004/health` 기준
checkpointer·경험정리 DB는 연결됐고, 메인 서버는 아직 `disconnected`다.

## 남은 완료 조건

다음은 메인 서버 또는 연동 환경이 준비돼야 검증할 수 있다.

- `block`·`block_kind` 운영 스키마와 AI 읽기 권한
- `GET /templates`의 확정 카탈로그 응답
- `POST /commit`, `GET /commit/{request_id}`의 멱등·409·422·응답 유실 복구
- 되돌리기 성공·충돌·만료
- AI → 메인 서버 API 키 값 합의

이 조건이 충족되기 전까지 `EXPERIENCE_MAP_ENABLED` 기본값은 `false`로 유지한다.
최종 `dev` PR에는 #301, #305~#310을 각각 닫는 `Closes` 행을 넣는다.
