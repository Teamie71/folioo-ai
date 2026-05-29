# GCS 직접 R/W 클라이언트

## Purpose
signed URL 을 경유하지 않고 IAM 직접 인증으로 템플릿과 잡 산출물을 GCS 에 읽고 쓰는 워커 측 클라이언트를 구축한다.

## Requirements
- IAM 직접 인증으로 `templates/**` GET 과 `jobs/{job_id}/...`(current.pptx/current.pdf/previews) PUT/GET 을 수행한다(signed URL 미경유).
- 재생성 경로는 Main commit 전 canonical key 를 덮어쓰지 않도록 `jobs/{job_id}/attempts/{attempt_id}/...` 에 staging 업로드한 뒤, callback 성공 후 GCS copy 로 canonical current.pptx/current.pdf/preview 에 promote 한다.
- 프리뷰 key canonical 규칙은 `jobs/{job_id}/previews/slide-{slide_order:02d}.jpg` 이며 PPTX/PDF 경로는 §9.1 을 따른다.
- 로컬 임시 파일은 `/tmp` 작업 디렉터리에서 처리하고 작업 종료 시 정리한다.

## Approach
`apps/pptx-worker/features/visualization/storage/`(예: `gcs_client.py`)에 GCS 클라이언트를 둔다. 버킷 `folioo-visualizations` 에 워커 SA 의 IAM 직접 R/W 권한을 부여한다. 워커는 signed URL 을 발급하지 않고 canonical `gcsPreviewKey` 만 콜백하며 signed 변환은 메인이 담당한다(spec 06 참조). 재생성 attempt key 는 사용자 signed URL 대상이 아니며, promote 실패 시 가능한 범위에서 사전 백업한 이전 canonical 객체로 롤백한다. `attempts/` 와 `rollback/` prefix 는 재시도·장애 분석을 위해 즉시 삭제하지 않고, 버킷 lifecycle rule 또는 운영 GC 로 보존 기간을 관리한다.

## Verification
- GCS PUT/GET 이 IAM 직접 인증으로 동작하는지(signed URL 미경유) 확인한다.
- 프리뷰/PPTX/PDF 경로가 canonical 규칙을 준수하는지 확인한다.
- 재생성 attempt key 업로드와 callback 성공 후 promote, promote 실패 시 rollback 시도를 확인한다.
- 작업 종료 후 로컬 임시 파일이 정리되는지 확인한다.
