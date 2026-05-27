---
id: "1.04"
phase: 1
title: "soffice 렌더 래퍼 (soffice→PDF→JPG)"
spec: "specs/phase-1/04-soffice-render.md"
depends_on: []
blocks: ["1.05", "1.06", "1.08", "1.09"]
estimate: "M"
status: "done"
completed_at: "2026-05-26"
owner: ""
sprint: ""
---

# Task 1.04 — soffice 렌더 래퍼 (soffice→PDF→JPG)

> Spec: [`specs/phase-1/04-soffice-render.md`](../../specs/phase-1/04-soffice-render.md)

## 의존성

- 독립 task — soffice/pdftoppm 렌더 래퍼 (leaf). 1.05·1.06·1.08·1.09 의 렌더 기반이라 우선 착수 권장. PPTX 패키징은 1.11, GCS 입출력은 1.12 로 분리(구 1.04 분할).

## 사전 준비

- [ ] 컨테이너에 libreoffice-impress·poppler-utils·Noto CJK 폰트 설치 (이미지 빌드는 1.10)

## 구현 체크리스트

- [x] soffice 래퍼: `--headless` + 변환마다 `UserInstallation` 격리 + 별도 서브프로세스, 30~60s 타임아웃 후 SIGKILL+1회 재시도, 종료 시 임시 디렉터리 정리
- [x] `pdftoppm` JPG(`-r 150`): 전체(`-f`/`-l` 없이) / 단일 페이지(`-f N -l N`) 모드
- [x] 모든 임시 파일 `/tmp` 작업 디렉터리 처리 + 종료 시 전부 삭제 (완전 무상태)
- [x] 누적 변환 카운터를 1.01 인스턴스 재활용 로직과 연동 (worker-spec.md §8.3)

## Definition of Done

- [x] soffice 동시 호출에도 `UserInstallation` 충돌 없이 PDF 생성, 타임아웃 시 SIGKILL+재시도 동작
- [x] `pdftoppm` 전체/단일 모드가 N장/1장 산출
- [x] 변환 종료 후 `/tmp` 작업 디렉터리가 비워짐

## 리스크 / 메모

- soffice 메모리 누수: 변환마다 서브프로세스 격리로 1~2GB 즉시 회수.
- 입력 PPTX 는 1.11(pack 결과) 또는 1.07(current.pptx), 산출 JPG 는 1.06 QA·프리뷰의 입력.
