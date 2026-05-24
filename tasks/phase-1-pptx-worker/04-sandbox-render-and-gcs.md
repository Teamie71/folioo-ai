---
id: "1.04"
phase: 1
title: "샌드박스 처리 — PPTX 도구 체인·soffice 렌더·GCS 직접 R/W"
spec: "specs/phase-1/04-sandbox-render-and-gcs.md"
depends_on: []
blocks: ["1.05", "1.06", "1.08", "1.09"]
estimate: "L"
status: "todo"
owner: ""
sprint: ""
---

# Task 1.04 — 샌드박스 처리 — PPTX 도구 체인·soffice 렌더·GCS 직접 R/W

> Spec: [`specs/phase-1/04-sandbox-render-and-gcs.md`](../../specs/phase-1/04-sandbox-render-and-gcs.md)

## 의존성

- 독립 task — 워커 인프라 레이어(도구 체인·soffice·GCS). 다른 task 의존 없음 (leaf). 다수 task(05·06·08·09)의 기반이라 우선 착수 권장.

## 사전 준비

- [ ] 컨테이너에 libreoffice-impress·poppler-utils·Noto CJK 폰트 설치 (이미지 빌드는 1.10)
- [ ] GCS 버킷 `folioo-visualizations` IAM 직접 R/W 권한(워커 SA) 확인

## 구현 체크리스트

- [ ] Anthropic PPTX 도구 체인 래핑: `unpack`/`clean`/`pack`/`validate` + 실패 시 `repair()` 재검증
- [ ] presentation.xml 미선택 슬라이드 sldId 제거 → clean 연계 (Step 2)
- [ ] soffice 래퍼: `--headless` + 변환마다 `UserInstallation` 격리 + 별도 서브프로세스, 30~60s 타임아웃 후 SIGKILL+1회 재시도, 종료 시 임시 디렉터리 정리
- [ ] `pdftoppm` JPG(`-r 150`): 전체(`-f`/`-l` 없이) / 단일 페이지(`-f N -l N`) 모드
- [ ] GCS 클라이언트(IAM 직접): `templates/**` GET, `jobs/{job_id}/...`(current.pptx/pdf/previews) PUT/GET
- [ ] 모든 임시 파일 `/tmp` 작업 디렉터리 처리 + 종료 시 전부 삭제 (완전 무상태)

## Definition of Done

- [ ] template.pptx unpack→미선택 제거→clean→pack 후 검증 통과·선택 슬라이드만 잔존 확인
- [ ] soffice 동시 호출에도 `UserInstallation` 충돌 없이 PDF 생성, 타임아웃 시 SIGKILL+재시도 동작
- [ ] `pdftoppm` 전체/단일 모드가 N장/1장 산출, GCS PUT/GET IAM 직접 동작, 종료 후 `/tmp` 비워짐

## 리스크 / 메모

- soffice 메모리 누수: 변환마다 서브프로세스 격리로 1~2GB 즉시 회수. 누적 변환 카운터는 1.01 인스턴스 재활용 로직과 연동.
- GCS key canonical: `jobs/{job_id}/previews/slide-{slide_order:02d}.jpg`.
