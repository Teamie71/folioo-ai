# PPTX Layout Action 적용

## Purpose
워커 내부 `layout_actions` 를 OOXML 에 먼저 반영한 뒤 기존 text fills 를 적용해, 메인 백엔드 계약을 바꾸지 않고도 resize, linked background 조정, row 재배치를 최종 PPTX/PDF/preview 에 반영한다.

## Requirements
- `SlideEditor.apply_layout_actions()` 를 추가하고 pipeline 에서 `apply_fills()` 전에 실행한다.
- 1차 action 으로 `resize_shape`, `resize_linked_shape`, `relayout_row`, marker 색상을 `output_text_color` 로 대체하는 text style 적용을 지원한다.
- `layout_actions` 는 `currentFills` 와 callback payload 에 섞지 않고 워커 내부 데이터로만 전달한다.
- Text shape 와 linked background 의 width/position 변경은 OOXML 좌표와 크기를 일관되게 수정하고, row 재배치는 group gap 과 item 순서를 보존한다.
- 적용 결과는 pack/validate 이후 PPTX, PDF, preview 에 반영되며 메인은 기존 `currentFills` 와 `gcsPreviewKey` 만 수신한다.

## Approach
`SlideEditor` 는 기존 `apply_fills()` 의 text/remove/chart 책임을 유지하고, geometry 변경은 별도 메서드로 분리한다. Pipeline 은 preflight 가 계산한 `layout_actions` 를 먼저 적용한 뒤 marker text replacement 와 fill 적용을 수행한다. Marker color 대체는 유형 슬라이드의 `#FF0000` 이 최종 결과에 남지 않도록 `output_text_color` 를 우선 사용하고, 없으면 안전 fallback 색상을 사용한다. 실패한 layout action 은 slide-level 오류로 격리해 잘못된 geometry 가 success 경로로 올라가지 않게 한다.

## Verification
- `resize_linked_shape` 적용 후 chip text box 와 background shape 의 폭이 모두 변경되는지 확인한다.
- `relayout_row` 적용 후 같은 group item 들의 x 좌표가 순서와 최소 gap 을 만족하는지 검증한다.
- Text fill 적용 후 최종 텍스트에 `#FF0000` marker 색상이 남지 않고 `output_text_color` 가 적용되는지 확인한다.
- Callback payload 에 `layout_actions` 가 포함되지 않고 기존 `currentFills` 구조만 유지되는지 검증한다.
- Layout action 적용 후 pack/validate/render 가 성공하고 preview 에 geometry 변경이 반영되는지 확인한다.
