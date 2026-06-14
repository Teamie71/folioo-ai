# PPTX Deterministic Text Fit Preflight

## Purpose
LLM 생성 전후에 deterministic capacity hint 와 heuristic text measurement 를 적용해, 렌더 이후에 발견되는 chip 줄바꿈, 텍스트 겹침, 과도한 폰트 축소를 사전에 줄인다.

## Requirements
- LLM Slot→Fill prompt 에 `placeholder_text`, `example_text`, 예시 줄 수, `max_lines`, `nowrap`, length hint 를 제공해 처음부터 slot 용량에 맞는 텍스트를 생성하도록 유도한다.
- LLM 결과에 대해 heuristic text width 측정과 10~15% 안전 여유를 적용하고, `nowrap`, `max_lines`, row overflow 위반을 렌더 전에 감지한다.
- `inline_label_group` 은 font shrink 보다 text/background width 조정, row relayout, gap 축소, 약칭 또는 텍스트 재요청, 실패 처리 순서로 대응한다.
- `basic_text_area` 는 max lines 검사, 요약, `min_font_pt` 까지의 제한적 font shrink 순서로 대응하고 8pt 이하 축소로 실패를 숨기지 않는다.
- fit 결과와 실패 사유는 structured log 로 남기며, 1차에서는 Visual QA 가 geometry action 을 직접 생성하지 않는다.

## Approach
Preflight 는 LLM fill 결정 직후와 OOXML 적용 전에 실행되는 deterministic 단계로 둔다. Slot capacity 는 v2 metadata 의 placeholder/example/layout group 정보를 사용하고, 실제 font metric 대신 문자군별 heuristic 측정으로 시작한다. Fitting 은 layout type 별 정책을 따르며, 성공 시 워커 내부 `layout_actions` 를 만들고 실패 시 짧은 대체 텍스트 요청 또는 slide-level 실패로 수렴한다. 최종 렌더의 잔여 clipping/overlap 검출은 기존 Visual QA 흐름에 맡긴다.

## Verification
- `OpenAI API` 같은 공백 포함 chip 텍스트가 `nowrap` 조건에서 임의 줄바꿈되지 않는지 확인한다.
- chip 텍스트 길이가 늘어날 때 text box 와 background width 가 함께 늘어나도록 `layout_actions` 가 계산되는지 검증한다.
- 같은 row 의 chip 들이 gap 축소와 relayout 후에도 겹치지 않는지 확인한다.
- row overflow 가 렌더 전 약칭 fallback 또는 실패로 분류되는지 검증한다.
- `basic_text_area` 가 `min_font_pt` 아래로 폰트를 줄이지 않고 요약 또는 실패로 수렴하는지 확인한다.
