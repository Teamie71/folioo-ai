---
id: "1.12"
phase: 1
title: "GCS 직접 R/W 클라이언트"
spec: "specs/phase-1/12-gcs-client.md"
depends_on: []
blocks: ["1.05", "1.06"]
estimate: "S"
status: "done"
completed_at: "2026-05-26"
owner: ""
sprint: ""
---

# Task 1.12 — GCS 직접 R/W 클라이언트

> Spec: [`specs/phase-1/12-gcs-client.md`](../../specs/phase-1/12-gcs-client.md)

## 의존성

- 독립 task — IAM 직접 인증 GCS R/W 클라이언트 (leaf). 1.05·1.06 의 GCS 입출력 기반. (구 1.04 에서 분리)

## 사전 준비

- [ ] GCS 버킷 `folioo-visualizations` IAM 직접 R/W 권한(워커 SA) 확인

## 구현 체크리스트

- [x] GCS 클라이언트(IAM 직접): `templates/**` GET, `jobs/{job_id}/...`(current.pptx/pdf/previews) PUT/GET (signed URL 미경유)
- [x] preview key canonical `jobs/{job_id}/previews/slide-{slide_order:02d}.jpg`, PPTX/PDF 경로 §9.1
- [x] 로컬 임시 파일 작업 디렉터리 처리 + 종료 시 정리

## Definition of Done

- [x] GCS PUT/GET 이 IAM 직접 인증으로 동작 (signed URL 미경유)
- [x] preview/PPTX/PDF 경로가 canonical 규칙 준수 확인
- [x] 작업 종료 후 로컬 임시 파일 정리 확인

## 리스크 / 메모

- 워커는 signed URL 발급 안 함 — `gcsPreviewKey` 만 콜백, 메인이 signed 변환 (1.06 참조).
