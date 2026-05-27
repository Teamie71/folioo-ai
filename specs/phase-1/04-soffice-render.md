# soffice 렌더 래퍼 (soffice → PDF → JPG)

## Purpose
무상태 작업 디렉터리에서 PPTX 를 헤드리스 LibreOffice 로 PDF 변환하고 `pdftoppm` 으로 페이지별 JPG 를 생성하는 렌더 래퍼를 구축한다. 생성·재생성·QA·템플릿 등록이 모두 이 래퍼 위에서 슬라이드 이미지를 얻는다.

## Requirements
- soffice 변환은 `--headless` + 변환마다 `UserInstallation` 격리 + 별도 서브프로세스로 띄우고, 30~60초 타임아웃 후 SIGKILL+1회 재시도하며 종료 시 임시 디렉터리를 정리한다.
- `pdftoppm` 으로 PDF→페이지별 JPG(`-r 150`)를 생성하되, 전체(`-f`/`-l` 없이) 또는 단일 페이지(재생성 시 `-f N -l N`) 모두 지원한다.
- 모든 임시 산출물은 `/tmp` 하위 작업 디렉터리에서 처리하고 변환 종료 시 전부 삭제한다(완전 무상태).
- 누적 변환 카운터를 spec 01 의 인스턴스 재활용 로직과 연동한다(worker-spec.md §8.3).

## Approach
`apps/pptx-worker/features/visualization/pptx/` 에 soffice 래퍼(`soffice_render.py` 등)를 둔다. soffice 는 메모리 누수 경향이 있으므로 프로세스 격리로 변환마다 1~2GB 를 즉시 회수한다. 입력 PPTX 는 spec 11(pack 결과) 또는 재생성 시 `current.pptx`, 산출 JPG 는 spec 06 QA·프리뷰의 입력이다. 컨테이너 이미지에는 libreoffice-impress·poppler-utils·Noto CJK 폰트를 사전 설치한다(§8.3.5, 이미지 빌드는 spec 10).

## Verification
- soffice 변환이 동시 호출에도 `UserInstallation` 충돌 없이 PDF 를 생성하고, 타임아웃 시 SIGKILL+재시도가 동작하는지 검증한다.
- `pdftoppm` 전체/단일 페이지 모드가 각각 N장/1장 JPG 를 산출하는지 확인한다.
- 변환 종료 후 `/tmp` 작업 디렉터리가 비워지는지 확인한다.
