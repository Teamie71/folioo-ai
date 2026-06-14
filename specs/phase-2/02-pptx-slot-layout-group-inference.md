# PPTX Slot/Layout Group 추론

## Purpose
컴파일된 v2 metadata 에 slot capacity 와 layout group 계약을 추가해, 텍스트 fitting 과 chip/label 재배치가 shape 단위 추측이 아니라 구조화된 계약을 기준으로 동작하게 만든다.

## Requirements
- Slot descriptor 에 `marker_color`, `placeholder_text`, `example_text`, `example_char_count`, `example_line_count`, `output_text_color`, `min_font_pt`, `max_font_pt`, `max_lines`, `nowrap`, `layout_group_id`, `fit_policy`, `allowed_actions` 를 포함한다.
- `role_hint` 는 선택 필드로만 다루고, LLM prompt 와 slot 추론의 1차 힌트는 `placeholder_text` 와 예시 reference 를 사용한다.
- 반복되는 짧은 label/chip 패턴은 높은 신뢰도일 때만 `inline_label_group` 으로 묶고, 그 외 editable slot 은 `basic_text_area` 또는 `unknown` fallback 으로 둔다.
- 작은 1:1 배경 도형은 `item_background` 로 연결하고, 여러 text slot 을 담는 큰 상자는 `container_shape` 로 분류해 개별 resize 대상에서 제외한다.
- `origin` 템플릿의 특정 slide index 나 shape id 에 의존하지 않고 색상 convention, slide pair, bbox, 겹침, 반복 구조를 조합해 추론한다.

## Approach
추론 로직은 template compiler 의 후처리 단계에서 수행해 런타임이 이미 구조화된 `slots` 와 `layout_groups` 를 읽도록 한다. `inline_label_group` 은 flow, gap, bbox alignment, linked background 신뢰도를 함께 평가하고, 애매한 경우 억지 분류하지 않는다. `item_background` 는 텍스트 slot 을 감싸는 작은 무텍스트 shape 에만 부여하고, 큰 카드나 섹션 배경은 `container_shape` 로만 기록한다. 기존 `currentFills` 의 `text`/`remove`/`chart` 계약은 유지하고 layout 관련 정보는 워커 내부 metadata 로 제한한다.

## Verification
- 첫 슬라이드 하단 기술 스택 chip 들이 하나의 `inline_label_group` 으로 묶이는지 확인한다.
- 각 chip 텍스트 slot 이 대응하는 1:1 `item_background` 와 연결되는지 검증한다.
- 여러 text slot 을 포함하는 큰 카드 배경이 `container_shape` 로 분류되고 `resize_linked_shape` 대상에서 제외되는지 확인한다.
- `role_hint` 가 없어도 `placeholder_text` 와 예시 reference 만으로 LLM prompt 가 구성되는지 검증한다.
- 애매한 그룹 후보가 `unknown` 또는 `basic_text_area` 로 fallback 되고 warning 으로 남는지 확인한다.
