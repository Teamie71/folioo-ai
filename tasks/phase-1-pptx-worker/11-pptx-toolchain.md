---
id: "1.11"
phase: 1
title: "PPTX 도구 체인 (unpack/clean/pack/validate + 슬라이드 제거)"
spec: "specs/phase-1/11-pptx-toolchain.md"
depends_on: []
blocks: ["1.05", "1.06"]
estimate: "S"
status: "done"
completed_at: "2026-05-25"
owner: ""
sprint: ""
---

# Task 1.11 — PPTX 도구 체인 (unpack/clean/pack/validate + 슬라이드 제거)

> Spec: [`specs/phase-1/11-pptx-toolchain.md`](../../specs/phase-1/11-pptx-toolchain.md)

## 의존성

- 독립 task — 패키지 수준 PPTX 도구 체인 어댑터 (leaf). 1.05·1.06 의 unpack/pack/validate 기반. (구 1.04 에서 분리)

## 사전 준비

- [x] Anthropic PPTX 스킬 도구 체인(`unpack`/`clean`/`pack`/`validate`) 사용 가능 여부 확인

## 구현 체크리스트

- [x] `unpack`/`clean`/`pack`/`validate` 래핑 + 실패 시 `repair()` 재검증
- [x] `presentation.xml` 미선택 슬라이드 `sldId` 제거 → clean 연계 (Step 2)
- [x] 모든 작업 `/tmp` 작업 디렉터리 처리 + 잔여물 미잔존

## Definition of Done

- [x] template.pptx unpack→미선택 제거→clean→pack 후 검증 통과·선택 슬라이드만 잔존 확인
- [x] `validate` 실패 시 `repair()` 후 재검증 동작
- [x] 작업 종료 후 `/tmp` 작업 디렉터리가 비워짐

## 리스크 / 메모

- 슬라이드 XML 내부 편집(텍스트/차트)은 1.03 SlideEditor 책임 — 본 레이어는 패키지 수준 unpack/pack/validate·슬라이드 단위 제거만.
- `pack` 결과 `.pptx` 는 1.04 soffice 렌더의 입력.
