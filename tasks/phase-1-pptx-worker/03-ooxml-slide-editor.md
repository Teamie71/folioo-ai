---
id: "1.03"
phase: 1
title: "OOXML 슬라이드 편집 엔진 (SlideEditor)"
spec: "specs/phase-1/03-ooxml-slide-editor.md"
depends_on: []
blocks: ["1.05", "1.06", "1.24"]
estimate: "M"
status: "done"
completed_at: "2026-05-26"
owner: ""
sprint: ""
---

# Task 1.03 — OOXML 슬라이드 편집 엔진 (SlideEditor)

> Spec: [`specs/phase-1/03-ooxml-slide-editor.md`](../../specs/phase-1/03-ooxml-slide-editor.md)

## 의존성

- 독립 task — `defusedxml` 기반 순수 XML 편집기. 외부 서비스/다른 task 의존 없음 (leaf). `ooxml-editing.md` §4.4 클래스 골격을 채운다.

## 사전 준비

- [x] 샘플 슬라이드 XML(텍스트 도형 + 차트 graphicFrame 포함) 픽스처 확보
- [x] `defusedxml.minidom` 의존성 확인

## 구현 체크리스트

- [x] `extract_slots()` — `<p:sp>`/`<p:graphicFrame>` 스캔 → shape_id(`cNvPr/@id`)·EMU 좌표/크기·current_text·is_title_placeholder·font_size_pt·kind
- [x] `apply_fills()` — 평평한 `{ "<shape_id>": {...} }` 맵의 `text`/`remove`/`chart` action 적용 (래퍼 없음)
- [x] `_replace_text()` — 첫 rPr/pPr 서식 보존, `<a:p>` 단위 줄바꿈, `xml:space="preserve"`, sz=pt×100, is_title→b="1"
- [x] 차트 `_replace_chart_cache()` — `numCache`/`strCache`/`ptCount`/`c:f` 일관 갱신, 타입 고정, `.xlsx` 미동기 (ADR-0003)
- [x] 식별자는 `cNvPr/@id` 의존 (`@name` 비의존)

## Definition of Done

- [x] `extract_slots()` 가 텍스트/차트 Slot 을 모두 잡고 EMU·폰트 정확 추출 검증
- [x] `text` 적용 후 그림자/그라데이션/정렬 등 원본 서식 보존 + 폰트 오버라이드 반영 검증
- [x] `remove` 가 `<p:sp>` 트리 제거, 차트 캐시 3곳 일관·타입 불변 검증

## 리스크 / 메모

- 차트는 graphicFrame rels → `/ppt/charts/chartN.xml` 경유. 캐시 세 곳(`numCache`/`strCache`/`ptCount`) 중 하나만 갱신하면 렌더 깨짐 — 동시 갱신 필수.
- `font_size_override`·`is_title` 는 차트 fill 에 적용 안 함.
