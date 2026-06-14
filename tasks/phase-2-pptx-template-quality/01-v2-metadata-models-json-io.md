---
id: "2.01"
phase: 2
title: "v2 메타데이터 모델과 JSON 입출력 기반"
spec: "specs/phase-2/01-pptx-template-v2-compiler.md"
depends_on: ["1.08"]
blocks: ["2.02"]
estimate: "M"
status: "done"
completed_at: "2026-06-14"
owner: ""
sprint: ""
---

# Task 2.01 — v2 메타데이터 모델과 JSON 입출력 기반

> Spec: [`specs/phase-2/01-pptx-template-v2-compiler.md`](../../specs/phase-2/01-pptx-template-v2-compiler.md)
> GitHub Issue: [#256](https://github.com/Teamie71/folioo-ai/issues/256)

## 의존성

- 1.08 (템플릿 등록 파이프라인) — 기존 `scripts/templates/` CLI와 `features.visualization.templates` 구조 위에 v2 컴파일러 기반을 추가한다.

## 사전 준비

- [x] 현재 `features/visualization/templates/` 모듈 구조와 `build_meta.py` 출력 계약 확인
- [x] 기존 `templates/origin/` 은 개선 전 버전으로 보고, `docs/ppt-v3.pptx` 를 `templates/ppt-v3/` 신규 등록 입력 자산으로 확인

## 구현 체크리스트

- [x] `features/visualization/templates/` 에 v2 metadata/reference dataclass 또는 TypedDict 모델 추가
- [x] `schema_version: 2`, `template_id`, `runtime_slides`, `slots`, `layout_groups` 기본 payload 생성기 추가
- [x] `reference.json` 기본 payload와 JSON normalize/sort-key writer 추가
- [x] `scripts/templates/compile_template.py` CLI 골격과 `template_dir`, `--out`, `--check`, `--strict` 인자 파싱 추가
- [x] `tests/test_features/test_visualization/test_template_v2_compiler.py` 에 JSON writer와 CLI 골격 단위 테스트 추가

## Definition of Done

- [x] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [x] `compile_template.py --help` 가 동작한다
- [x] 빈 추출 결과에서도 v2 JSON skeleton 이 deterministic 하게 생성된다
- [x] JSON normalize 비교 유틸리티가 key 순서 차이를 무시하고 의미 차이는 감지한다

## 리스크 / 메모

- 기존 `build_meta.py` 는 운영자 검토용 v1 meta 초안 생성 책임을 유지한다. v2 compiler 와 역할을 섞지 않는다.
