---
id: "1.21"
phase: 1
title: "Cloud Run 배포 환경, Secrets, GCS/IAM 운영 체크 완성"
spec: "specs/phase-1/10-cloud-run-deployment-config.md"
depends_on: ["1.04", "1.10", "1.11", "1.12"]
blocks: ["1.26"]
estimate: "M"
status: "todo"
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

- [ ] GitHub Issue #236 본문과 기존 Cloud Run 배포 YAML 확인
- [ ] Secret 값 없이 필요한 Secret 이름/용도만 문서화할 수 있는지 확인
- [ ] 실제 GCP 프로젝트/서비스 계정 세부값을 공개 문서에 적지 않는 원칙 확인

## 구현 체크리스트

- [ ] Worker -> Main callback/context 조회에 필요한 `MAIN_BACKEND_URL`, `MAIN_BACKEND_API_KEY` 연결 방식 확인
- [ ] LLM 호출에 필요한 `OPENROUTER_API_KEY` 및 모델 관련 환경변수 연결 방식 확인
- [ ] PPTX toolchain 실행 경로에 필요한 환경변수 확인
- [ ] Cloud Run YAML 또는 배포 문서에 Secret Manager 참조 방식 명시
- [ ] Cloud Run require-authentication 모델 유지 여부 확인
- [ ] Cloud Tasks 서비스 계정의 `roles/run.invoker` 부여 절차 명시
- [ ] Worker 서비스 계정의 GCS 직접 R/W 권한 범위 명시
- [ ] `folioo-visualizations` bucket template 읽기 권한 확인 절차 정리
- [ ] `jobs/{job_id}/...` 산출물 쓰기/읽기 권한 확인 절차 정리
- [ ] `tasks/phase-1-pptx-worker/12-gcs-client.md`의 IAM 확인 체크 상태를 실제 검증 결과와 맞춤
- [ ] `soffice`, `pdftoppm`, Noto CJK 폰트, markitdown 설치 확인 절차의 보장 위치를 문서화
- [ ] `tasks/phase-1-pptx-worker/04-soffice-render.md`의 컨테이너 도구 설치 확인 체크를 실제 검증 방법과 연결

## Definition of Done

- [ ] Cloud Run 설정 또는 배포 문서에 필수 env/secret/IAM/GCS 항목 누락 없음
- [ ] Secret 값이 저장소에 노출되지 않음
- [ ] Cloud Tasks OIDC 인증과 Worker -> Main `X-API-Key` 인증 책임이 구분되어 있음
- [ ] GCS IAM 직접 R/W 검증 절차가 task 문서와 일치
- [ ] 컨테이너 도구 설치 확인 체크박스가 실제 검증 방법과 연결됨

## 리스크 / 메모

- circular import, dependency group, 앱 부팅 smoke는 1.20 범위다.
- signed URL은 Worker가 사용하지 않고 Main Backend가 프론트 다운로드용으로만 발급한다는 계약을 유지한다.
