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

## 의존성

- 3.07 (첨부 파일 저장) — GCS 임시 object 와 삭제 정책
- 3.11 (Router·Fallback) — 추출 불가 시 `file_unreadable` fallback 으로 분기

## 사전 준비

- [ ] 기존 `app/schemas/pdf_extraction.py` 와 PDF 추출 구현 재사용 가능 여부 확인
- [ ] OCR 모델 선택과 호출 비용·타임아웃 확인
- [ ] 파일별·전체 context 길이 제한값 결정

## 구현 체크리스트

- [ ] `nodes/file_processor.py` + `prompts/file_processor.py`
- [ ] 파일 파서: TXT·DOCX·PPTX
- [ ] OCR 모델: PDF·PNG·JPG/JPEG
- [ ] 두 종류가 섞이면 각각 처리 후 **입력 순서대로** 이어 붙임
- [ ] 파일별 추출 결과와 source hash 저장
- [ ] 파일별·전체 context 길이 제한
- [ ] 추출 결과를 checkpoint 에 저장한 뒤 GCS 원본 즉시 삭제
- [ ] **실패 2종 분기**: 품질 문제 → `fallback` / 시스템 오류 → 노드 실패(자동 재시도)

## Definition of Done

- [ ] 품질 문제로 추출 불가 시 `fallback`, 시스템 오류 시 노드 실패로 **구분된다**
- [ ] 추출 완료 뒤에는 원본 파일 없이 재시도된다
- [ ] 추출 노드 실패 시에는 GCS 원본으로 재시도된다
- [ ] 파서 형식과 OCR 형식을 섞어 업로드해도 순서가 유지된다
- [ ] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- **두 실패의 구분이 이 태스크의 핵심이다.** 손상된 PDF 를 올린 사용자에게 재시도 버튼을 보여주면 몇 번을 눌러도 같은 결과다.
