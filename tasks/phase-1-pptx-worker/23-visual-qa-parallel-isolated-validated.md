---
id: "1.23"
phase: 1
title: "Visual QA 안정화: 병렬 처리, 슬라이드별 실패 격리, 수정 후 검증"
spec: "specs/phase-1/06-visual-qa-fix-verify.md"
depends_on: ["1.05", "1.06", "1.07"]
blocks: ["1.25", "1.26"]
estimate: "L"
status: "todo"
owner: ""
sprint: ""
---

# Task 1.23 — Visual QA 안정화: 병렬 처리, 슬라이드별 실패 격리, 수정 후 검증

> Spec: [`specs/phase-1/06-visual-qa-fix-verify.md`](../../specs/phase-1/06-visual-qa-fix-verify.md)
> GitHub Issue: [#238](https://github.com/Teamie71/folioo-ai/issues/238)

## 의존성

- 1.05 (Phase 1 초기 생성 파이프라인 오케스트레이션) — 초기 생성의 QA 결과가 전체 job summary와 preview 콜백으로 수렴한다.
- 1.06 (시각 QA + Fix-and-Verify 루프 + 프리뷰 업로드) — QA 병렬성, 예외 격리, fix 후 검증을 직접 보강하는 대상이다.
- 1.07 (Phase 2 재생성/재시도 파이프라인) — regenerate/retry 경로의 단일 slide QA에도 동일한 실패 격리 원칙이 적용된다.

## 사전 준비

- [ ] GitHub Issue #238 본문과 현재 QA 순차 실행/예외 전파 경로 확인
- [ ] LLM vision 호출 rate limit과 Cloud Run 리소스 기준의 동시성 제한 필요 여부 확인
- [ ] 기존 `slide_preview_ready`, `slide_preview_error`, `all_completed` callback 계약 확인

## 구현 체크리스트

- [ ] 현재 `check_slide` 호출과 fix loop 실행 순서 분석
- [ ] QA 대상 슬라이드를 병렬로 검사하도록 구조 개선
- [ ] 필요한 경우 LLM rate limit과 Cloud Run 리소스를 고려한 동시성 제한 지점 추가
- [ ] 빠르게 통과한 슬라이드는 다른 슬라이드 QA 완료를 기다리지 않고 preview 업로드/콜백 가능하게 정리
- [ ] `check_slide` 예외를 해당 슬라이드의 QA 실패 결과로 변환
- [ ] preview 업로드 실패를 해당 슬라이드의 `slide_preview_error`로 수렴
- [ ] QA/fix 중 특정 슬라이드 실패가 전체 Cloud Tasks retry를 유발하지 않도록 정리
- [ ] 전체 실패와 일부 실패의 job summary/errorCode가 기존 callback 계약과 맞는지 검증
- [ ] 자동 수정 후 pack 산출물이 구조 검증을 통과하는지 확인하는 경로 추가/보강
- [ ] 검증 실패 시 성공 preview/upload/callback으로 진행하지 않도록 차단
- [ ] 영향 받은 슬라이드만 재 QA한다는 정책 유지
- [ ] 최대 2회 실패 시 retryable 값과 message가 일관되게 전달되는지 확인

## Definition of Done

- [ ] QA 병렬 처리 테스트 통과
- [ ] 슬라이드별 QA 예외 격리 테스트 통과
- [ ] preview 업로드 실패의 슬라이드별 error 콜백 테스트 통과
- [ ] fix-and-verify 후 구조 검증 실패 차단 테스트 통과
- [ ] 전체 성공/일부 실패/전체 실패 summary가 기존 계약과 일치
- [ ] `uv run ruff check .` 및 관련 worker visualization 테스트 통과

## 리스크 / 메모

- 재생성 canonical GCS key promote/rollback 설계는 1.25에서 처리한다.
- 완료 전 최소 1회 시각 QA 원칙과 "재검증은 영향 받은 슬라이드만" 정책은 유지한다.
