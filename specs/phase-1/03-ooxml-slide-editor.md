# OOXML 슬라이드 편집 엔진 (SlideEditor)

## Purpose
디자이너 서식을 보존하면서 슬라이드 XML 의 Slot 을 자동 추출하고 LLM 이 결정한 Fill 을 적용하는 `SlideEditor`(`extract_slots` / `apply_fills`)를 구현한다.

## Requirements
- `extract_slots()` 가 `<p:sp>` 와 `<p:graphicFrame>` 를 스캔해 `shape_id`(=`cNvPr/@id`)·좌표/크기(EMU)·`current_text`·`is_title_placeholder`·`font_size_pt`·`kind`(text/image/chart) Slot 디스크립터를 생성한다.
- `apply_fills()` 가 평평한 `{ "<shape_id>": { action, text, font_size_override, is_title } }` 맵을 받아 `text`/`remove`/`chart` action 을 XML 에 적용한다(래퍼 없음).
- 텍스트 교체는 첫 `<a:rPr>`/`<a:pPr>` 서식을 보존하고, 줄바꿈을 `<a:p>` 단위로 분리하며, 공백은 `xml:space="preserve"` 로 처리한다(일괄 치환 금지).
- 차트 Fill 은 `chartN.xml` 의 `numCache`/`strCache`/`ptCount`/`c:f` 만 일관 갱신하고 차트 타입은 고정, 임베디드 `.xlsx` 는 동기화하지 않는다(ADR-0003).
- XML 파서는 `defusedxml.minidom` 을 사용하고 식별자는 `cNvPr/@name` 이 아닌 `cNvPr/@id` 에 의존한다.

## Approach
`apps/pptx-worker/features/visualization/pptx/` 에 `SlideEditor` 를 두고 `ooxml-editing.md` §4.4 의 클래스 골격(`_get_shape_id`/`_describe_shape`/`_describe_graphic_frame`/`_replace_text`/`_replace_chart_cache`)을 채운다. 사전 Slot 명세에 의존하지 않고 위치·크기·현재 텍스트만으로 LLM 이 역할을 추론하므로, 편집기는 결정적(deterministic) XML 조작만 담당하고 의미 판단은 하지 않는다. 차트는 graphicFrame rels 로 차트 파트를 찾아 들어가 캐시 세 곳을 동시에 갱신한다(§4.4.1).

## Verification
- 샘플 슬라이드 XML 에서 `extract_slots()` 가 텍스트/차트 Slot 을 모두 잡아내고 EMU 좌표·폰트 크기를 정확히 읽는지 검증한다.
- `apply_fills()` 의 `text` action 후 그림자/그라데이션/정렬 등 원본 서식이 보존되고 폰트 크기 오버라이드(`sz` = pt×100)가 반영되는지 확인한다.
- `remove` action 이 해당 `<p:sp>` 를 트리에서 제거하는지 확인한다.
- 차트 Fill 적용 후 `numCache`/`strCache`/`ptCount`/`c:f` 가 일관되고 차트 타입이 변하지 않는지 검증한다.
