---
id: "1.08"
phase: 1
title: "템플릿 등록 파이프라인 스크립트 (오프라인 운영자/CI)"
spec: "specs/phase-1/08-template-registration-pipeline.md"
depends_on: ["1.04"]
blocks: []
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 1.08 — 템플릿 등록 파이프라인 스크립트 (오프라인 운영자/CI)

> Spec: [`specs/phase-1/08-template-registration-pipeline.md`](../../specs/phase-1/08-template-registration-pipeline.md)

## 의존성

- 1.04 (soffice 렌더) — `build_meta.py` 가 soffice→PDF→pdftoppm 슬라이드 JPG·그리드 썸네일 생성에 1.04 의 렌더 래퍼를 재사용한다(중복 구현 회피). ⚠ 모듈 공유 의존: 08 은 런타임 워커와 분리된 오프라인 도구지만 렌더 래퍼가 1.04 와 겹쳐 순차 처리로 둔다(사용자 확정). PPTX 도구 체인 1.11·GCS 1.12 는 불요.

## 사전 준비

- [ ] `scripts/templates/` · `templates/_schema/` 디렉터리 신규 생성
- [ ] 메타 작성 보조 LLM(`common/llm`) + markitdown 사용 확인

## 구현 체크리스트

- [ ] `templates/_schema/categories.json` — §3.3 표준 Enum 단일 소스(cover/toc/overview/problem/process/outcome/chart/visual/text/closing)
- [ ] `scripts/templates/build_meta.py` — soffice/pdftoppm JPG+그리드 thumbnail → markitdown 임시 텍스트 → LLM 초안{category,description,best_for} + 카테고리 내 알파벳 id → meta.json 초안
- [ ] `scripts/templates/validate_template.py` — 필수 필드 스키마·category Enum(unknown 실패)·slide_index 0..N-1 연속 & PPTX 수 일치·id 중복 검증 (CI 실행)
- [ ] 카테고리 분포 권장 범위는 경고만(실패 아님)
- [ ] `slide_index`/`template_file` 자동(운영자 미수정), 의미 필드는 LLM 초안 후 운영자 검토

## Definition of Done

- [ ] `build_meta.py` 가 JPG·그리드 썸네일·임시 텍스트 생성 + 알파벳 id 채운 meta.json 초안 작성 검증
- [ ] `validate_template.py` 가 필수누락·Enum 밖·slide_index 불연속/불일치·중복 id 를 각각 실패로 잡음 검증
- [ ] Enum 미확장 신규 category 가 실패하는지 검증

## 리스크 / 메모

- 런타임 시각화 워커(`apps/pptx-worker/`)는 이 단계에 미관여 — 오프라인 운영자/CI 전용. 빌드 단계 LLM 호출이라 런타임 사용자 비용 영향 없음(§3.5.4).
