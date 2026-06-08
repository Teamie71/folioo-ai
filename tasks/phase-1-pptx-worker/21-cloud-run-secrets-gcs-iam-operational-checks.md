---
id: "1.21"
phase: 1
title: "Cloud Run 배포 환경, Secrets, GCS/IAM 운영 체크 완성"
spec: "specs/phase-1/10-cloud-run-deployment-config.md"
depends_on: ["1.04", "1.10", "1.11", "1.12"]
blocks: ["1.26"]
estimate: "M"
status: "done"
completed_at: "2026-05-31"
owner: ""
sprint: ""
---

# Task 1.21 — Cloud Run 배포 환경, Secrets, GCS/IAM 운영 체크 완성

> Spec: [`specs/phase-1/10-cloud-run-deployment-config.md`](../../specs/phase-1/10-cloud-run-deployment-config.md)
> GitHub Issue: [#236](https://github.com/Teamie71/folioo-ai/issues/236)

## 의존성

- 1.04 (soffice 렌더 래퍼) — 컨테이너 도구 설치 확인 항목이 이 렌더링 도구 체인을 전제로 한다.
- 1.10 (Cloud Run 배포 구성) — Cloud Run YAML, service spec, require-authentication 설정의 후속 운영 보강이다.
- 1.11 (PPTX 도구 체인) — Anthropic PPTX toolchain 실행 경로와 런타임 환경변수 확인이 필요하다.
- 1.12 (GCS 직접 R/W 클라이언트) — 워커 서비스 계정의 template 읽기 및 job 산출물 R/W 권한 확인이 필요하다.

## 사전 준비

- [x] GitHub Issue #236 본문과 기존 Cloud Run 배포 YAML 확인
- [x] Secret 값 없이 필요한 Secret 이름/용도만 문서화할 수 있는지 확인
- [x] 실제 GCP 프로젝트/서비스 계정 세부값을 공개 문서에 적지 않는 원칙 확인

## 구현 체크리스트

- [x] Worker -> Main callback/context 조회에 필요한 `MAIN_BACKEND_URL`, `MAIN_BACKEND_API_KEY` 연결 방식 확인
- [x] LLM 호출에 필요한 `OPENROUTER_API_KEY` 및 모델 관련 환경변수 연결 방식 확인
- [x] PPTX toolchain 실행 경로에 필요한 환경변수 확인
- [x] Cloud Run YAML 또는 배포 문서에 Secret Manager 참조 방식 명시
- [x] Cloud Run require-authentication 모델 유지 여부 확인
- [x] Cloud Tasks 서비스 계정의 `roles/run.invoker` 부여 절차 명시
- [x] Worker 서비스 계정의 GCS 직접 R/W 권한 범위 명시
- [x] `folioo-visualizations` bucket template 읽기 권한 확인 절차 정리
- [x] `jobs/{job_id}/...` 산출물 쓰기/읽기 권한 확인 절차 정리
- [x] `tasks/phase-1-pptx-worker/12-gcs-client.md`의 IAM 확인 체크 상태를 실제 검증 결과와 맞춤
- [x] `soffice`, `pdftoppm`, Noto CJK 폰트, markitdown 설치 확인 절차의 보장 위치를 문서화
- [x] `tasks/phase-1-pptx-worker/04-soffice-render.md`의 컨테이너 도구 설치 확인 체크를 실제 검증 방법과 연결

## Definition of Done

- [x] Cloud Run 설정 또는 배포 문서에 필수 env/secret/IAM/GCS 항목 누락 없음
- [x] Secret 값이 저장소에 노출되지 않음
- [x] Cloud Tasks OIDC 인증과 Worker -> Main `X-API-Key` 인증 책임이 구분되어 있음
- [x] GCS IAM 직접 R/W 검증 절차가 task 문서와 일치
- [x] 컨테이너 도구 설치 확인 체크박스가 실제 검증 방법과 연결됨

## 구현 결과

- `deploy/pptx-worker/cloud-run-service.yaml` 에 `MAIN_BACKEND_URL`, `MAIN_BACKEND_TIMEOUT`, `OPENROUTER_BASE_URL`, `LLM_MODEL_NAME`, `FILE_PROCESSOR_MODEL_NAME`, `MAIN_BACKEND_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_PPTX_SKILL_DIR` 를 명시했다.
- `MAIN_BACKEND_API_KEY` 와 `OPENROUTER_API_KEY` 는 Secret Manager `secretKeyRef` 로만 연결하고 실제 값은 저장소에 두지 않는다.
- PPTX toolchain 은 이미지 내부 `/app/apps/pptx-worker/toolchain` 으로 번들해 별도 Secret/volume 없이 runtime smoke 에서 `ensure_available()` 로 확인한다.
- `deploy/pptx-worker/README.md` 에 API 활성화, Artifact Registry, 서비스 계정, Secret, GCS bucket/IAM, Cloud Tasks queue, Cloud Run deploy, 콘솔 체크리스트를 정리했다.
- 실제 GCP IAM 부여와 버킷 객체 업로드는 운영자가 수행해야 하므로 `tasks/phase-1-pptx-worker/12-gcs-client.md` 의 IAM 확인 체크는 완료로 바꾸지 않았다.

## 리스크 / 메모

- circular import, dependency group, 앱 부팅 smoke는 1.20 범위다.
- signed URL은 Worker가 사용하지 않고 Main Backend가 프론트 다운로드용으로만 발급한다는 계약을 유지한다.
