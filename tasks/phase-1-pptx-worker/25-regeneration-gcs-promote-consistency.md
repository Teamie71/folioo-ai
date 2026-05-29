---
id: "1.25"
phase: 1
title: "재생성 산출물 정합성: Canonical GCS 덮어쓰기 전 Commit/Promote 계약"
spec: "specs/phase-1/07-phase2-regenerate-retry-pipeline.md"
depends_on: ["1.07", "1.12", "1.23"]
blocks: ["1.26"]
estimate: "L"
status: "done"
completed_at: "2026-05-29"
owner: ""
sprint: ""
---

# Task 1.25 — 재생성 산출물 정합성: Canonical GCS 덮어쓰기 전 Commit/Promote 계약

> Spec: [`specs/phase-1/07-phase2-regenerate-retry-pipeline.md`](../../specs/phase-1/07-phase2-regenerate-retry-pipeline.md)
> GitHub Issue: [#240](https://github.com/Teamie71/folioo-ai/issues/240)

## 의존성

- 1.07 (Phase 2 재생성/재시도 파이프라인) — current.pptx/current.pdf/preview를 갱신하는 regenerate 경로의 후속 정합성 보강이다.
- 1.12 (GCS 직접 R/W 클라이언트) — canonical key와 attempt/staging key 업로드·읽기 계약을 다룬다.
- 1.23 (Visual QA 안정화) — 재생성 산출물 promote 전에 QA/fix 결과가 슬라이드별로 안정적으로 수렴해야 한다.

## 사전 준비

- [x] GitHub Issue #240 본문과 현재 regenerate upload/callback 순서 확인
- [x] Main Backend commit, callback, signed URL 발급 책임이 Worker와 어떻게 분리되는지 확인
- [x] 프론트가 사용하는 canonical key 규칙과 export 경로 확인

## 구현 체크리스트

- [x] regenerate/retry 경로에서 PPTX/PDF/preview가 업로드되는 순서 확인
- [x] `slide_regenerated` 콜백 전후로 canonical key가 언제 바뀌는지 정리
- [x] 콜백 실패, Main 5xx, Main 4xx, upload 실패, QA 실패 케이스별 현재 결과를 표로 정리
- [x] 초기 생성 경로와 재생성 경로의 canonical key 책임 차이 분리
- [x] 재생성 산출물을 먼저 attempt/staging key에 업로드하는 방식 검토
- [x] canonical `current.pptx`, `current.pdf`, `previews/slide-XX.jpg`가 언제 promote되는지 정의
- [x] Main Backend가 commit 후 promote를 요청/승인하는 계약이 필요한지 정리
- [x] Worker repo에서 구현 가능한 범위와 Main Backend 후속 구현 범위 분리
- [x] Main commit 성공 전 canonical key overwrite가 발생하지 않도록 업로드 순서 조정
- [x] 실패한 attempt 산출물이 프론트 signed URL 대상이 되지 않도록 key 반환 규칙 점검
- [x] retry 중복 실행과 attempt key 충돌이 생기지 않도록 idempotency key 또는 attempt id 활용 방안 정리
- [x] 성공/실패 callback payload가 기존 Main 계약과 호환되는지 확인

## Definition of Done

- [x] Main commit 성공 전 canonical PPTX/PDF/preview overwrite가 일어나지 않음
- [x] 콜백 실패 시 DB 상태와 사용자 노출 산출물이 이전 completed 버전으로 일관됨
- [x] 성공 경로에서만 canonical key가 최신 결과로 promote됨
- [x] 실패한 attempt key가 프론트 preview/export 경로로 노출되지 않음
- [x] 관련 정합성 테스트 또는 계약 테스트 통과
- [x] `uv run ruff check .` 및 관련 worker 테스트 통과

## 구현 결과

- 재생성 attempt key: `jobs/{job_id}/attempts/{attempt_id}/current.pptx`, `current.pdf`, `previews/slide-XX.jpg`
- canonical key: 기존 `jobs/{job_id}/current.pptx`, `current.pdf`, `previews/slide-XX.jpg` 유지
- Worker 순서: attempt preview/PPTX/PDF 업로드 → `slide_regenerated` callback 성공 → canonical promote
- `slide_regenerated.gcsPreviewKey` 는 프론트 signed URL 규칙을 깨지 않도록 canonical preview key 만 보낸다.

| 케이스 | Worker 결과 | 사용자 노출 canonical |
| --- | --- | --- |
| QA 실패 | `slide_preview_error`, attempt/canonical 업로드 없음 | 이전 completed 유지 |
| attempt upload 실패 | `slide_preview_error`, attempt key 를 callback 하지 않음 | 이전 completed 유지 |
| Main callback 4xx/5xx/timeout | Cloud Tasks retry, promote 미실행 | 이전 completed 유지 |
| promote 성공 | attempt 산출물을 canonical 로 copy | 최신 결과 |
| promote 중 copy 실패 | 가능한 범위에서 backup canonical 로 rollback 후 Cloud Tasks retry | rollback 성공 시 이전 completed 유지 |
| 중복 retry | 같은 idempotency key 기반 attempt id 재사용 | 같은 canonical 대상에 멱등 promote |

## 리스크 / 메모

- Main Backend의 DB 트랜잭션, CAS, signed URL 발급 구현은 별도 저장소 책임이다.
- 필요한 callback/promote 계약 변경은 1.26 handoff에 반영한다.
