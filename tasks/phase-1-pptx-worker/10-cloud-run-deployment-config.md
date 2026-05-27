---
id: "1.10"
phase: 1
title: "Cloud Run 배포 구성 (시각화 워커)"
spec: "specs/phase-1/10-cloud-run-deployment-config.md"
depends_on: ["1.01"]
blocks: []
estimate: "S"
status: "done"
completed_at: "2026-05-27"
owner: ""
sprint: ""
---

# Task 1.10 — Cloud Run 배포 구성 (시각화 워커)

> Spec: [`specs/phase-1/10-cloud-run-deployment-config.md`](../../specs/phase-1/10-cloud-run-deployment-config.md)

## 의존성

- 1.01 (서비스 스캐폴드) — Cloud Run 에 올리는 대상이 이 FastAPI 진입점이며, require-authentication·OIDC IAM 위임 인증 모델이 01 과 정합해야 한다. Dockerfile/서비스 사양은 진입점이 존재해야 컨테이너화·검증 가능. (런타임 전체 기능 검증은 05·07 완료 후가 이상적이나 구조적 선행은 01)

## 사전 준비

- [x] GCP 프로젝트/리전·Cloud Run 배포 권한 확인
- [x] Cloud Tasks 서비스 계정 식별(`roles/run.invoker` 부여 대상)

## 구현 체크리스트

- [x] Cloud Run Service 사양: 메모리 4GB(limit 5GB)·2 vCPU·`/tmp` 1GB+·`concurrency=1`·min 0(민감 시 1)·max 20·timeout 1800s
- [x] `JAVA_TOOL_OPTIONS=-Xmx512m` (LibreOffice 내부 JVM 힙 캡)
- [x] Dockerfile(§8.3.5): ubuntu 22.04 + libreoffice-impress/core + poppler-utils + fonts-noto-cjk(+extra) + python + 필수 pip 패키지
- [x] require-authentication + OIDC 검증 Cloud Run IAM 위임, Cloud Tasks SA 에 `roles/run.invoker`
- [x] 인터뷰 챗과 빌드/배포 분리, `common/` 직접 import (ADR-0001) — Docker 빌드는 필요한 모듈만 COPY

## Definition of Done

- [x] 배포 서비스가 4GB/limit 5GB·2 vCPU·concurrency=1·timeout 1800s·max 20 으로 기동, JAVA 힙 캡 적용 확인
- [x] 이미지에 soffice·pdftoppm·Noto CJK·markitdown·필수 pip 설치 + `common/` import 가능 검증
- [x] `roles/run.invoker` Cloud Tasks SA OIDC 호출만 통과, 권한 없는 호출 IAM 거부 검증

## 리스크 / 메모

- max-instances 는 Cloud Tasks `maxConcurrentDispatches`(=LLM rate limit)와 일치시킬 것.
- Dockerfile 자체는 일찍 작성 가능하나 통합 배포 검증은 런타임(05·07) 완료 후 수행 권장.
