---
id: "1.22"
phase: 1
title: "Cloud Tasks 중복 Push 및 In-flight 멱등 처리 강화"
spec: "specs/phase-1/01-worker-service-scaffold.md"
depends_on: ["1.01", "1.02", "1.05", "1.07"]
blocks: ["1.26"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 1.22 — Cloud Tasks 중복 Push 및 In-flight 멱등 처리 강화

> Spec: [`specs/phase-1/01-worker-service-scaffold.md`](../../specs/phase-1/01-worker-service-scaffold.md)
> GitHub Issue: [#237](https://github.com/Teamie71/folioo-ai/issues/237)

## 의존성

- 1.01 (시각화 워커 서비스 스캐폴드 및 Cloud Tasks Push 핸들러) — generate/regenerate push handler의 처리 전 멱등 체크를 보강한다.
- 1.02 (메인 백엔드 콜백/컨텍스트 클라이언트) — 처리 전 Main internal API 상태 조회와 필요한 claim/check 계약 검토가 필요하다.
- 1.05 (Phase 1 초기 생성 파이프라인 오케스트레이션) — generate 중복 push가 초기 생성 파이프라인 중복 실행으로 이어지지 않게 해야 한다.
- 1.07 (Phase 2 재생성/재시도 파이프라인) — regenerate/retry 중복 push가 단일 slide 파이프라인 중복 실행으로 이어지지 않게 해야 한다.

## 사전 준비

- [ ] GitHub Issue #237 본문과 Cloud Tasks at-least-once delivery 전제 확인
- [ ] 현재 terminal skip과 processable status 조건을 테스트로 재현
- [ ] Main Backend CAS가 담당해야 할 범위와 worker-local guard로 가능한 범위 구분

## 구현 체크리스트

- [ ] generate push 처리 전 Main Backend 상태 조회 조건 확인
- [ ] regenerate/retry push 처리 전 slide/job 상태 조회 조건 확인
- [ ] terminal 상태 skip과 processable 상태 진입 조건을 테스트로 고정
- [ ] `idempotencyKey`가 callback event key와 worker execution key에서 어떻게 쓰이는지 분리 확인
- [ ] 같은 payload 동시 도착 시 worker-local guard, Main internal API claim/check, 기존 상태 조회 API 확장 중 구현 방안 결정
- [ ] Cloud Run `concurrency=1`만으로 보장되지 않는 중복 재전송 케이스 문서화
- [ ] skip/retry/fatal 응답 코드가 Cloud Tasks 재시도 모델과 맞는지 확인
- [ ] 동일 job generate push가 이미 처리 중일 때 중복 파이프라인 실행 방지
- [ ] 동일 slide regenerate push가 이미 처리 중일 때 중복 파이프라인 실행 방지
- [ ] 처리 대상이 아닌 상태는 200 ACK skip 유지
- [ ] retryable/fatal 실패가 기존 callback 계약을 깨지 않도록 유지

## Definition of Done

- [ ] generate terminal 재전송이 재실행 없이 200 ACK 되는 테스트 통과
- [ ] generate in-flight 중복 push가 실제 파이프라인을 중복 실행하지 않는 테스트 통과
- [ ] regenerate terminal/비대상 상태 push가 200 skip 되는 테스트 통과
- [ ] regenerate in-flight 중복 push가 단일 실행으로 수렴하는 테스트 통과
- [ ] retryable/fatal/skip 응답 분류가 Cloud Tasks 재시도 정책과 문서상 일치
- [ ] `uv run ruff check .` 및 관련 worker 테스트 통과

## 리스크 / 메모

- Worker는 Postgres에 직접 접근하지 않는다.
- Main Backend의 DB CAS, quota 차감, stuck recovery 구현이 필요하면 1.26 handoff에 명확히 남긴다.
