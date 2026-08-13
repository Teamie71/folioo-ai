---
id: "3.12"
phase: 3
title: "파일처리 노드 (파서·OCR)"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.07", "3.11"]
blocks: ["3.23"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.12 — 파일처리 노드 (파서·OCR)

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 5-2, 9절 10번
> PR: EM-12 · 브랜치 `feat/{issue}-experience-map-file-processor`
> GitHub Issue: [#307](https://github.com/Teamie71/folioo-ai/issues/307)

## 의존성

- 3.07 (첨부 파일 저장) — GCS 임시 object 와 삭제 정책
- 3.11 (Router·Fallback) — 추출 불가 시 `file_unreadable` fallback 으로 분기

## 사전 준비

- [x] 기존 `app/schemas/pdf_extraction.py` 와 PDF 추출 구현 재사용 가능 여부 확인
- [x] OCR 모델 선택과 호출 비용·타임아웃 확인
- [x] 파일별·전체 context 길이 제한값 결정

## 구현 체크리스트

- [x] `nodes/file_processor.py` + `prompts/file_processor.py`
- [x] 파일 파서: TXT·DOCX·PPTX
- [x] OCR 모델: PDF·PNG·JPG/JPEG
- [x] 두 종류가 섞이면 각각 처리 후 **입력 순서대로** 이어 붙임
- [x] 파일별 추출 결과와 source hash 저장
- [x] 파일별·전체 context 길이 제한
- [x] 추출 결과를 checkpoint 에 저장한 뒤 GCS 원본 즉시 삭제
- [x] **실패 2종 분기**: 품질 문제 → `fallback` / 시스템 오류 → 노드 실패(자동 재시도)

## Definition of Done

- [x] 품질 문제로 추출 불가 시 `fallback`, 시스템 오류 시 노드 실패로 **구분된다**
- [x] 추출 완료 뒤에는 원본 파일 없이 재시도된다
- [x] 추출 노드 실패 시에는 GCS 원본으로 재시도된다
- [x] 파서 형식과 OCR 형식을 섞어 업로드해도 순서가 유지된다
- [x] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **두 실패의 구분이 이 태스크의 핵심이다.** 손상된 PDF 를 올린 사용자에게 재시도 버튼을 보여주면 몇 번을 눌러도 같은 결과다.
- 결과: `1271 passed` (신규 19). ruff check·format 통과.
- **markitdown 이 손상된 zip 에 예외를 던지지 않는다.** 바이트를 평문으로 해석해 깨진 글자(`偋̄扲潫敮`)를 돌려준다. 그대로 두면 그 쓰레기가 LLM 까지 흘러가 사용자 경험 정리에 들어간다. `zipfile` 로 컨테이너 온전성을 먼저 확인한다 — 업로드 검증(3.07)은 첫 4바이트 signature 만 본다.
- 추출 결과가 비어 있으면 `FileUnreadableError` 다. 빈 문서·이미지만 있는 문서·스캔 품질 문제가 여기 걸린다.
- 이미 추출한 파일은 건너뛴다. 재시도할 때 원본이 없어도 이어서 간다.
- 전체 길이 상한을 넘으면 **뒤쪽을 버린다.** 앞에 올린 파일이 대개 더 중요하다.
- `markitdown[docx,pptx]` 를 main 의존성으로 올렸다. 이미 template-tools·pptx-worker 그룹에 같은 버전이 있어 해석 위험이 없다.
- OCR 은 기존 `get_file_processor_llm()` 을 재사용한다 (timeout 120, max_retries 0).
