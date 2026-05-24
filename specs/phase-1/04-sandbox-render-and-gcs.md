# 샌드박스 처리 — PPTX 도구 체인 · soffice 렌더 · GCS 직접 R/W

## Purpose
무상태 샌드박스에서 PPTX unpack/clean/pack/validate, soffice→PDF→JPG 렌더, GCS 직접 읽기/쓰기를 수행하는 워커 인프라 레이어를 구축한다.

## Requirements
- Anthropic PPTX 스킬 도구 체인(`unpack`/`clean`/`pack`/`validate`)을 래핑해 PPTX 해제·고아 정리·재패키징·스키마 검증과 실패 시 `repair()` 재검증을 제공한다.
- soffice 변환은 `--headless` + 변환마다 `UserInstallation` 격리 + 별도 서브프로세스로 띄우고, 30~60초 타임아웃 후 SIGKILL+1회 재시도하며 종료 시 임시 디렉터리를 정리한다.
- `pdftoppm` 으로 PDF→페이지별 JPG(`-r 150`)를 생성하되, 전체(`-f`/`-l` 없이) 또는 단일 페이지(재생성 시 `-f N -l N`) 모두 지원한다.
- GCS 클라이언트가 IAM 직접 인증으로 `templates/**` GET 과 `jobs/{job_id}/...`(current.pptx/current.pdf/previews) PUT/GET 을 수행한다(signed URL 미경유).
- 모든 임시 파일은 `/tmp` 하위 작업 디렉터리에서 처리하고 작업 종료 시 전부 삭제한다(완전 무상태).

## Approach
`apps/pptx-worker/features/visualization/pptx/` 에 soffice 래퍼·GCS 클라이언트·도구 체인 어댑터를 둔다. soffice 는 메모리 누수 경향이 있으므로 프로세스 격리로 변환마다 1~2GB 를 즉시 회수하고, 누적 변환 카운터는 spec 01 의 인스턴스 재활용 로직과 연동한다(worker-spec.md §8.3). GCS key canonical 규칙은 `jobs/{job_id}/previews/slide-{slide_order:02d}.jpg` 이며 prview/PPTX/PDF 경로는 §9.1 을 따른다. 컨테이너 이미지에는 libreoffice-impress·poppler-utils·Noto CJK 폰트를 사전 설치한다(§8.3.5).

## Verification
- 샘플 template.pptx 를 unpack→미선택 슬라이드 제거→clean→pack 했을 때 검증을 통과하고 선택 슬라이드만 남는지 확인한다.
- soffice 변환이 동시 호출에도 `UserInstallation` 충돌 없이 PDF 를 생성하고, 타임아웃 시 SIGKILL+재시도가 동작하는지 검증한다.
- `pdftoppm` 전체/단일 페이지 모드가 각각 N장/1장 JPG 를 산출하는지 확인한다.
- GCS PUT/GET 이 IAM 직접 인증으로 동작하고 작업 종료 후 `/tmp` 작업 디렉터리가 비워지는지 확인한다.
