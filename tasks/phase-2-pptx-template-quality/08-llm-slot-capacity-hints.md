---
id: "2.08"
phase: 2
title: "LLM Slot prompt capacity hint 적용"
spec: "specs/phase-2/03-pptx-deterministic-text-fit-preflight.md"
depends_on: ["1.05", "2.05"]
blocks: ["2.09", "2.10"]
estimate: "S"
status: "todo"
owner: ""
sprint: ""
---

# Task 2.08 — LLM Slot prompt capacity hint 적용

> Spec: [`specs/phase-2/03-pptx-deterministic-text-fit-preflight.md`](../../specs/phase-2/03-pptx-deterministic-text-fit-preflight.md)
> GitHub Issue: [#263](https://github.com/Teamie71/folioo-ai/issues/263)

## 의존성

- 1.05 (초기 생성 파이프라인) — LLM Call #2 Slot→Fill prompt 흐름에 capacity hint 를 넣는다.
- 2.05 (Slot capacity 계약 확장) — prompt 에 사용할 placeholder/example/max_lines/nowrap 정보가 metadata 에 있어야 한다.

## 사전 준비

- [ ] `features/visualization/agents/generation.py` 의 Slot→Fill prompt 구성 확인
- [ ] 기존 테스트에서 prompt snapshot 또는 fake LLM 입력 검증 방식 확인

## 구현 체크리스트

- [ ] Slot prompt payload 에 `placeholder_text`, `example_text`, `example_line_count`, `max_lines`, `nowrap` 추가
- [ ] length hint 문구를 `inline_label_group` 과 `basic_text_area` 에 맞게 생성
- [ ] 예시 텍스트는 복사 대상이 아니라 형식/길이 참고임을 prompt 에 명시
- [ ] role_hint 누락 시에도 placeholder/example 기반으로 prompt 가 생성되도록 테스트

## Definition of Done

- [ ] 작업 중 또는 완료 후 새 사용자 확인사항이 생기면 `tasks/phase-2-pptx-template-quality/18-user-signoff-and-operational-readiness.md` 에 추가했다.
- [ ] LLM Call #2 입력에 capacity hint 가 포함된다
- [ ] `OpenAI API` 같은 짧은 chip 예시가 한 줄 label 의 길이 힌트로 전달된다
- [ ] 기존 v1 slot payload 에서도 prompt 생성이 안전하게 fallback 한다

## 리스크 / 메모

- 이 task 는 LLM 입력 개선만 다룬다. 실제 fit 계산과 layout action 생성은 후속 task 에서 처리한다.
