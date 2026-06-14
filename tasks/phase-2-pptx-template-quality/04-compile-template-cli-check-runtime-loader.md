---
id: "2.04"
phase: 2
title: "compile_template CLI check 모드와 런타임 v2 fail-fast"
spec: "specs/phase-2/01-pptx-template-v2-compiler.md"
depends_on: ["2.03"]
blocks: ["2.14", "2.16"]
estimate: "M"
status: "done"
completed_at: "2026-06-14"
owner: ""
sprint: ""
---

# Task 2.04 — compile_template CLI check 모드와 런타임 v2 fail-fast

> Spec: [`specs/phase-2/01-pptx-template-v2-compiler.md`](../../specs/phase-2/01-pptx-template-v2-compiler.md)
> GitHub Issue: [#259](https://github.com/Teamie71/folioo-ai/issues/259)

## 의존성

- 2.03 (예시 슬라이드 reference 매칭) — CLI 가 실제 `meta.json`/`reference.json` 산출물을 만들 수 있어야 check 모드와 런타임 로딩을 검증할 수 있다.

## 사전 준비

- [x] 기존 generation pipeline 의 meta.json 로딩 지점 확인
- [x] `--out` 출력 디렉터리와 기본 템플릿 디렉터리 갱신 정책 확인

## 구현 체크리스트

- [x] `compile_template.py` 기본 실행이 template dir 의 `meta.json`/`reference.json` 을 갱신하도록 연결
- [x] `--out` 실행은 별도 디렉터리에 산출물을 기록하고 원본 디렉터리를 변경하지 않도록 구현
- [x] `--check` 는 현재 산출물과 새 산출물의 normalized JSON 을 비교해 exit code 를 반환
- [x] 런타임 템플릿 로더에서 `schema_version == 2` 가 아니면 fail fast 하도록 변경
- [x] 기존 v1 meta 사용 경로가 실패 메시지를 명확히 내는지 테스트 추가

## Definition of Done

- [x] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [x] `docs/ppt-v3.pptx` 기반으로 신규 등록한 템플릿 디렉터리에서 산출물이 최신이면 `compile_template.py <template_dir> --check` 가 성공한다
- [x] 산출물이 다르면 `--check` 가 non-zero 로 실패한다
- [x] `schema_version` 누락 또는 2가 아닌 `meta.json` 은 런타임에서 즉시 실패한다

## 리스크 / 메모

- 1차에서는 `runtime_template.pptx` 를 만들지 않는다. 선택 슬라이드 작업 파일 흐름은 기존 pipeline 을 사용한다.
