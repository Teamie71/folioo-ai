---
id: "1.26"
phase: 1
title: "Spec/Task 상태 정리 및 Main Backend Handoff 명확화"
spec: "specs/phase-1/10-cloud-run-deployment-config.md"
depends_on: ["1.20", "1.21", "1.22", "1.23", "1.24", "1.25"]
blocks: []
estimate: "M"
status: "done"
completed_at: "2026-05-31"
owner: ""
sprint: ""
---

# Task 1.26 — Spec/Task 상태 정리 및 Main Backend Handoff 명확화

> Spec: [`specs/phase-1/10-cloud-run-deployment-config.md`](../../specs/phase-1/10-cloud-run-deployment-config.md)
> GitHub Issue: [#241](https://github.com/Teamie71/folioo-ai/issues/241)

## 의존성

- 1.20 (PPTX Worker 런타임 부팅 및 이미지 패키징 차단 해소) — runtime/import/smoke 보강 결과를 문서 상태에 반영해야 한다.
- 1.21 (Cloud Run 배포 환경, Secrets, GCS/IAM 운영 체크 완성) — 배포 Secret/IAM/GCS 운영 체크 결과를 문서 상태에 반영해야 한다.
- 1.22 (Cloud Tasks 중복 Push 및 In-flight 멱등 처리 강화) — worker/main 멱등 책임 경계를 handoff에 반영해야 한다.
- 1.23 (Visual QA 안정화) — QA 병렬성, 슬라이드별 실패 격리, 수정 후 검증 결과를 spec/task에 반영해야 한다.
- 1.24 (생성 품질 계약 보강) — slot/chart/reason/Phase 2 guardrail 계약 변경을 관련 문서에 반영해야 한다.
- 1.25 (재생성 산출물 정합성) — canonical GCS promote와 Main Backend commit 책임 경계를 handoff에 반영해야 한다.

## 사전 준비

- [x] GitHub Issue #241 본문 확인
- [x] `docs/architecture/pptx-gen-plan-v6.md`, `specs/phase-1/*.md`, `tasks/phase-1-pptx-worker/*.md` 최신 상태 확인
- [x] Main Backend 저장소 또는 담당자에게 넘길 수 있는 공개 가능한 범위 확인

## 구현 체크리스트

- [x] `tasks/phase-1-pptx-worker/*.md`의 체크박스와 실제 검증 결과 비교
- [x] GCS IAM 직접 R/W 권한 확인 항목의 완료/미완료 상태 정리
- [x] 컨테이너 도구 설치 확인 항목의 완료/미완료 상태 정리
- [x] template registration GCS publish 또는 운영 배포 단계가 DoD에 빠져 있는지 확인
- [x] spec과 task가 서로 다른 processable status를 설명하는 부분을 실제 코드/설계 기준으로 정리
- [x] 1.20 런타임 부팅/패키징 차단 해소 결과를 관련 task/spec에 반영
- [x] 1.21 배포 환경/Secret/GCS/IAM 정리 결과를 관련 task/spec에 반영
- [x] 1.22 Cloud Tasks in-flight 멱등 처리 결과를 관련 task/spec에 반영
- [x] 1.23 Visual QA 안정화 결과를 관련 task/spec에 반영
- [x] 1.24 생성 품질 계약 보강 결과를 관련 task/spec에 반영
- [x] 1.25 재생성 산출물 정합성 계약 결과를 관련 task/spec에 반영
- [x] Main Backend의 사용자 인증/소유권 검증 범위 정리
- [x] Main Backend의 Postgres DB 상태 전이 및 CAS 책임 정리
- [x] Main Backend의 Cloud Tasks enqueue 및 idempotency key 생성 책임 정리
- [x] Main Backend의 SSE snapshot/event fan-out 책임 정리
- [x] Main Backend의 signed URL 발급 책임 정리
- [x] Main Backend의 재생성 quota 차감/보상 책임 정리
- [x] stuck recovery cron 또는 복구 정책 정리
- [x] export 가능 여부 재검증 책임 정리
- [x] Worker callback 수신 API와 `X-API-Key` 검증 책임 정리
- [x] 배포 전 필수 smoke/test 명령 목록 정리
- [x] Secret 값 없이 필요한 Secret 이름/용도만 문서화
- [x] IAM 권한 확인 절차를 공개 가능한 수준으로 문서화
- [x] Worker-only 범위와 외부 저장소 범위를 분리해서 기록
- [x] 완료되지 않은 항목은 완료로 표시하지 않도록 전체 문서 재점검

## Definition of Done

- [x] spec/task 문서의 완료 표시가 실제 검증 상태와 일치
- [x] Worker repo 내 후속 수정 task(1.20~1.25)의 결과가 문서에 반영됨
- [x] Main Backend 후속 구현 목록이 독립적으로 실행 가능한 수준으로 정리됨
- [x] Secret 값, 서비스 계정 세부값 등 민감 정보가 문서에 노출되지 않음
- [x] 최종 문서만 보고 운영 전 남은 작업을 구분할 수 있음

## 구현 결과

- Main Backend handoff 문서: [`docs/architecture/pptx-main-backend-handoff.md`](../../docs/architecture/pptx-main-backend-handoff.md)
- GCP/운영 설정 문서: [`deploy/pptx-worker/README.md`](../../deploy/pptx-worker/README.md)
- `specs/phase-1/06-visual-qa-fix-verify.md`, `specs/phase-1/10-cloud-run-deployment-config.md` 에 1.21/1.23 결과를 반영했다.
- Task 1.23 은 PR #243 병합과 테스트 증거를 기준으로 완료 처리했다.
- Task 1.12 의 실제 GCS IAM 확인 항목은 운영자가 GCP에서 검증해야 하므로 미완료 상태로 유지했다.

## 리스크 / 메모

- 이 task는 문서 정합성과 handoff가 범위다. 문서가 가리키는 코드 수정은 1.20~1.25에서 처리한다.
- Main Backend 저장소가 별도로 있다면, 이 task 결과를 그쪽 이슈 생성의 원본으로 사용할 수 있게 작성한다.
- 1.25 handoff에는 재생성 산출물 계약을 명시한다: Worker는 attempt key 업로드 후 `slide_regenerated` 2xx를 Main commit 성공으로 간주하고 canonical promote를 수행한다. Main은 실패한 attempt key를 signed URL 대상으로 노출하지 않고, `slide_regenerated` idempotency 처리와 promote 실패 재시도 중 사용자 노출 정책을 정리해야 한다.
- Main Backend는 `slide_regenerated` callback 직후 canonical signed URL 발급 시점이 GCS promote 완료보다 앞설 수 있음을 전제로, promote 완료 확인 전에는 이전 preview 유지 또는 pending 상태 노출 정책을 정해야 한다.
- Worker promote rollback 도 일부 GCS copy 실패 시 완전 원자성을 보장할 수 없으므로, Main/운영 handoff에는 부분 canonical 혼합 감지, 알림, 수동 복구 절차, job 단위 잠금/generation precondition 적용 가능성을 포함한다.
- `jobs/{job_id}/attempts/{attempt_id}/...` 및 `rollback/` 객체는 즉시 삭제하지 않는다. 배포/운영 handoff에서 bucket lifecycle rule 또는 주기적 GC로 보존 기간을 정한다.
