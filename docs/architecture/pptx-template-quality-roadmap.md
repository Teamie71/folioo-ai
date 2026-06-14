# FOLIOO 시각화 - 템플릿 품질 개선 로드맵

> 이 문서는 PPTX 생성 결과에서 발견된 텍스트 크기, 줄바꿈, 겹침, placeholder 잔존, chip/box 크기 불일치 문제를 개선하기 위한 작업 계획이다.
> 관련 문서: `template-system.md`, `ooxml-editing.md`, `qa-and-guardrails.md`, `pptx-gen-plan-v6.md`

## 0. MVP 결정 요약

1차 MVP는 템플릿을 완전히 자동 이해하는 범용 엔진을 만드는 단계가 아니다. 현재 템플릿 제작 convention 을 안정적인 계약으로 컴파일하고, 가장 크게 깨지는 `inline_label_group` 계열의 chip/label 품질 문제를 deterministic fitting 으로 줄이는 단계다.

확정된 방향:

- 메인 백엔드 계약은 변경하지 않는다.
- `currentFills` 는 기존 `text` / `remove` / `chart` 중심으로 유지한다.
- resize, move, relayout 은 워커 내부 `layout_actions` 로만 다룬다.
- 새 템플릿 metadata 는 `schema_version: 2` 를 필수로 둔다.
- 1차 런타임은 `schema_version == 2` 만 허용하고, 누락/불일치 시 fail fast 한다.
- `template_version` 은 1차에서 사용하지 않는다.
- 1차에서는 `runtime_template.pptx` 물리 파일을 만들지 않는다.
- 원본 `template.pptx` 는 그대로 두고, `meta.json` 에 runtime 대상 슬라이드만 표시한다.
- 예시 슬라이드는 출력 후보가 아니라 `reference.json` 생성과 추론에만 사용한다.
- 유형 슬라이드의 정확한 `#FF0000` 텍스트만 editable marker 로 본다.
- `#FF0000` 이 아닌 텍스트는 기본적으로 fixed/decorative 로 유지한다.
- 예시 슬라이드는 빨강/검정 검증 대상이 아니다.
- 유형 슬라이드가 기본 구조와 텍스트 서식의 원천이다.
- 예시 슬라이드에서는 `example_text`, 줄 수, 글자 수, marker 대체용 `output_text_color` 를 주로 추출한다.
- `font size`, `bold`, `alignment`, shape fill/border 는 기본적으로 유형 슬라이드 것을 유지한다.
- 한 텍스트 shape 안에서 editable marker 와 fixed label 을 섞는 run-level replacement 는 1차에서 지원하지 않는다.
- `placeholder_text` 는 LLM prompt 와 slot 추론에 반드시 사용한다.
- `role_hint` 는 선택 보조 필드다. 없어도 생성은 동작해야 한다.
- `layout_type` 은 slot 단위가 아니라 group 단위 개념이다.
- 1차 자동 추론은 `inline_label_group` 과 `item_background` 연결에 집중한다.
- 나머지 editable slot 은 `basic_text_area` 또는 안전 fallback 으로 처리한다.
- `container_shape` 는 1차에서 감지와 참고 정보로만 사용하고, 내부 재배치는 후속으로 둔다.
- 텍스트 폭 측정은 heuristic 기반으로 시작하고, 최종 렌더 문제는 기존 Visual QA 가 감지한다.
- 1차에서는 QA 구조를 크게 바꾸지 않는다. QA issue remedy 기반 구조화는 2차로 둔다.
- 기본 디버깅은 structured log 로 남긴다. GCS debug artifact 는 옵션으로만 둔다.

1차에서 하지 않는 일:

- 메인 DTO / SSE 계약 변경
- `runtime_template.pptx` 물리 생성
- mixed color run-level replacement
- 모든 layout type 자동 분류
- LLM 직접 좌표 수정
- container 내부 재배치
- QA 가 직접 geometry action 을 생성하는 구조

## 1. 배경

현재 템플릿 제작 방식은 다음 convention 을 가진다.

- 짝수 슬라이드: 실제 런타임에서 사용할 레이아웃 유형
- 홀수 슬라이드: 바로 앞 짝수 레이아웃을 실제 콘텐츠로 채운 예시
- 유형 슬라이드의 `#FF0000` 빨간 텍스트: 포트폴리오 내용으로 교체할 editable marker
- 유형 슬라이드의 빨간색이 아닌 텍스트: 양식 문구 또는 장식 문구로 유지

이 convention 은 템플릿 제작자가 PowerPoint 안에서 직관적으로 편집할 수 있다는 장점이 있다. 다만 현재 구현은 이 규칙을 알지 못하고, 텍스트가 들어 있는 도형 대부분을 editable/required slot 으로 취급한다. 또한 빨간 텍스트 주변의 배경 도형을 텍스트 slot 과 연결하지 못해, 텍스트만 줄이거나 줄바꿈하는 방식으로 문제를 피한다.

대표적인 증상은 다음과 같다.

- 기술 스택 chip 에서 `OpenAI API` 가 `OpenAI` / `API` 두 줄로 깨진다.
- 텍스트 박스는 좁은데 배경 박스만 커 보여, 사용자는 충분한 공간이 있다고 느낀다.
- chip 텍스트 길이에 따라 배경 상자와 옆 chip 이 함께 재배치되지 않는다.
- 카드 폭 대비 본문 텍스트 박스가 지나치게 좁아 불필요한 줄바꿈이 생긴다.
- 제목 박스 높이/폭이 부족해 카드 테두리와 겹친다.
- 이미지/도식 placeholder 가 실제 자산 없이 그대로 남는다.
- Visual QA 는 문제를 감지할 수 있지만, 현재 action 범위가 텍스트/폰트 크기 중심이라 구조적 문제를 고치지 못한다.

## 2. 목표

목표는 특정 `origin` 템플릿에만 맞춘 하드코딩이 아니라, 템플릿 convention 을 활용하면서도 다른 템플릿에도 확장 가능한 품질 개선 구조를 만드는 것이다.

핵심 원칙:

1. PPTX 안의 convention 을 템플릿 등록/검증 시점에 기계가 읽는 계약으로 컴파일한다.
2. 런타임 생성은 컴파일된 slot 계약과 layout group 계약을 기준으로 동작한다.
3. LLM 은 콘텐츠 선택과 요약에 집중한다.
4. 텍스트 fitting, 도형 resize, row relayout 은 deterministic 엔진이 담당한다.
5. Visual QA 는 1차에서는 기존 흐름을 유지하고, 최종 렌더 문제 감지 역할을 맡는다.
6. QA 가 geometry 를 직접 수정하는 구조는 2차 개선으로 분리한다.

## 3. 템플릿 컴파일 전략

### 3.1 산출물

1차에서는 원본 `template.pptx` 를 물리적으로 쪼개지 않는다. 대신 원본에서 runtime 대상 슬라이드와 예시 슬라이드를 구분해 metadata 를 만든다.

```text
templates/{template_id}/
├── template.pptx              # 디자이너가 관리하는 원본 PPTX
├── meta.json                  # 런타임 슬라이드 + slot/layout 계약
├── reference.json             # 예시 슬라이드에서 추출한 참고 정보
└── thumbnail.jpg
```

`meta.json` 과 `reference.json` 은 metadata 구조 버전을 명시한다. 1차 런타임은 v2 구조만 지원한다.

```json
{
  "schema_version": 2,
  "template_id": "origin",
  "runtime_slides": [],
  "slots": [],
  "layout_groups": []
}
```

`reference.json` 도 같은 구조 버전을 가진다.

```json
{
  "schema_version": 2,
  "template_id": "origin",
  "slide_pairs": [],
  "shape_matches": []
}
```

`schema_version` 은 템플릿 디자인 버전이 아니라 metadata 구조 버전이다. `template_version` 은 1차에서 사용하지 않는다. 템플릿 metadata 는 워커 내부 계약이므로 snake_case 를 사용한다.

런타임 필수 파일은 `template.pptx` 와 `meta.json` 이다. `reference.json` 은 컴파일/검증/디버깅 산출물이며, 런타임은 원칙적으로 `meta.json` 만으로 생성 가능해야 한다.

후속 단계에서 필요하면 `runtime_template.pptx` 를 만들 수 있다. 1차에서는 기존 파이프라인이 선택 슬라이드만 작업 파일에 남기는 흐름을 활용한다.

컴파일러 기본 동작은 템플릿 디렉토리의 산출물을 바로 갱신하는 것이다.

```bash
uv run python scripts/templates/compile_template.py templates/origin
```

위 명령은 `templates/origin/template.pptx` 를 읽고 `templates/origin/meta.json`, `templates/origin/reference.json` 을 다시 생성한다. 비교/검수용으로는 별도 output 과 check 모드를 제공한다.

```bash
uv run python scripts/templates/compile_template.py templates/origin \
  --out /tmp/origin-compiled

uv run python scripts/templates/compile_template.py templates/origin \
  --check
```

1차에서는 기존 `meta.json` / `reference.json` 의 수동 필드를 보존하지 않는다. `template.pptx` 와 `compile_template.py` 산출물이 source of truth 다. 수동 예외 처리가 필요해지면 후속 단계에서 `overrides.json` 같은 별도 파일을 검토한다.

컴파일러는 JSON 산출물을 deterministic 하게 쓴다.

```python
json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
```

`--check` 는 byte-for-byte 비교가 아니라 JSON parse 후 normalize 한 결과를 비교한다. 현재 파일과 새로 계산한 산출물의 normalized JSON 이 다르면 실패한다.

컴파일러는 계약 위반과 품질 위험을 구분한다.

```text
fail:
  - template.pptx 없음
  - schema 산출 불가
  - runtime 대상 슬라이드 없음
  - 유형 슬라이드에 #FF0000 editable marker 없음
  - 빨간 marker shape 안에 non-red run 이 섞임
  - 필요한 예시 slide pair 없음
  - editable slot 의 예시 shape 매칭 실패
  - meta/reference JSON 생성 실패

warning:
  - inline_label_group 후보이나 background 매칭 신뢰도가 낮음
  - editable slot 이 너무 좁음
  - output_text_color 를 예시에서 못 가져와 fallback 색상 사용
  - layout_type 이 basic_text_area fallback 으로 처리됨
  - container_shape 후보가 있으나 내부 배치 분석은 생략됨
```

기본은 계약 위반만 fail 처리한다. `strict` mode 는 운영 배포 전 품질 게이트다. 개발 중에는 기본 모드로 빠르게 반복하고, 서비스 반영 전이나 CI 에서는 `strict` mode 로 일부 warning 을 fail 로 승격한다.

`strict` mode 는 compile 과 validate 양쪽에서 지원한다.

```bash
uv run python scripts/templates/compile_template.py templates/origin --strict
uv run python scripts/templates/validate_template.py templates/origin --strict
```

컴파일 규칙:

- 유형 슬라이드는 runtime 후보로 등록한다.
- 예시 슬라이드는 runtime 후보에서 제외하고 `reference.json` 으로만 추출한다.
- 유형 슬라이드의 정확한 `#FF0000` 텍스트 shape 만 editable slot 으로 등록한다.
- `#FF0000` 이 아닌 텍스트는 fixed/decorative 로 유지한다.
- 유형 슬라이드의 빨간 텍스트는 최종 스타일이 아니라 marker 다.
- 예시 슬라이드에서 대응 shape 를 찾아 `example_text`, line count, char count, `output_text_color` 를 추출한다.
- 빨간 텍스트와 가까운 작은 배경 도형은 필요 시 `item_background` 로 연결한다.
- 여러 text slot 을 포함하는 큰 상자는 `container_shape` 로 감지하되, 1차에서는 개별 resize 대상에 넣지 않는다.

### 3.2 유형/예시 슬라이드 매칭

예시 슬라이드는 정답 데이터가 아니라 레이아웃 의도를 보여주는 reference 다. 런타임 결과에 포함하지 않는다.

활용 범위:

- editable slot 의 예시 텍스트 추출
- 예시 글자 수와 줄 수 추정
- marker 색상 대체용 output text color 추출
- 짧은 label/chip 그룹 여부 추론
- LLM 프롬프트의 길이/밀도 힌트 생성

유형 슬라이드의 editable shape 와 예시 슬라이드의 대응 shape 는 shape id 가 아니라 위치/크기 기반으로 매칭한다.

```text
유형 슬라이드                     예시 슬라이드
┌────────────────────┐          ┌────────┐
│ 정량적 성과 수치    │  bbox -> │ 150%   │
└────────────────────┘          └────────┘
     #FF0000 marker              실제 출력 색상/길이 참고
```

매칭 기준:

- bbox center distance
- bbox overlap ratio
- width/height similarity
- 텍스트 shape 여부
- 같은 slide pair 안의 상대 위치

예시 슬라이드에서 가져오지 않는 것:

- shape 위치
- shape 크기
- shape fill/border
- bold/alignment/font size 전체

예시 슬라이드에서 가져오는 것:

- `example_text`
- `example_line_count`
- `example_char_count`
- marker 대체용 `output_text_color`

### 3.3 색상 convention

1차 색상 규칙은 단순해야 한다.

```text
유형 슬라이드:
  #FF0000 = editable marker
  그 외 텍스트 = fixed/decorative

예시 슬라이드:
  색상 검증 대상 아님
  실제 출력 색상 참고용
```

주의사항:

- `#FF0000` 은 정확한 RGB 값만 인정한다.
- `#FE0000`, theme red, tint/shade red 는 editable marker 로 보지 않는다.
- 한 shape 안에 빨간 run 과 non-red run 이 섞인 경우는 1차에서 지원하지 않는다.
- fixed label 과 editable value 는 별도 텍스트 shape 로 분리하는 것을 템플릿 제작 규칙으로 둔다.

## 4. Slot / Layout 계약

### 4.1 Slot descriptor

현재 slot descriptor 는 `shape_id`, 좌표, 크기, current_text, font_size_pt, kind 정도만 포함한다. 1차에서는 다음 필드를 추가한다.

```json
{
  "shape_id": "26",
  "kind": "text",
  "editable": true,
  "required": true,
  "marker_color": "#FF0000",
  "placeholder_text": "사용 기술",
  "example_text": "OpenAI API",
  "example_char_count": 10,
  "example_line_count": 1,
  "output_text_color": "#000000",
  "x_emu": 3811270,
  "y_emu": 5636770,
  "w_emu": 670560,
  "h_emu": 322580,
  "font_size_pt": 10,
  "min_font_pt": 10,
  "max_font_pt": 15,
  "max_lines": 1,
  "nowrap": true,
  "layout_group_id": "auto_inline_group_1",
  "fit_policy": "resize_label",
  "role_hint": "tech_stack",
  "allowed_actions": ["text", "remove"]
}
```

필드 의미:

| 필드 | 의미 |
|---|---|
| `marker_color` | 유형 슬라이드에서 editable marker 로 감지한 색상. 1차에서는 `#FF0000` 만 인정 |
| `placeholder_text` | 유형 슬라이드의 빨간 문구. LLM prompt 와 content hint 로 사용 |
| `example_text` | 예시 슬라이드에서 위치/크기 기반으로 매칭한 실제 사용 예 |
| `example_char_count` / `example_line_count` | capacity 및 length hint 추론에 사용 |
| `output_text_color` | 예시 슬라이드의 대응 텍스트 색상. marker 색상 대체용 |
| `role_hint` | 선택 필드. 명확한 경우에만 컴파일러가 붙이는 내부 의미 라벨 |
| `layout_group_id` | slot 이 속한 layout group |
| `fit_policy` | slot 또는 group 에 적용할 fitting 전략 |
| `allowed_actions` | 메인에 저장 가능한 fill action. 1차에서는 기존 계약을 유지 |

`role_hint` 는 필수가 아니다. `placeholder_text` 가 1차 힌트이며, `role_hint` 가 없어도 생성은 정상 동작해야 한다.

### 4.2 Layout group

`layout_type` 은 slot 하나가 아니라 group 단위 개념이다.

```json
{
  "layout_groups": [
    {
      "group_id": "auto_inline_group_1",
      "layout_type": "inline_label_group",
      "flow": "horizontal",
      "item_shape_ids": ["26", "28", "30"],
      "container": {
        "x_emu": 3200000,
        "y_emu": 5600000,
        "w_emu": 4200000,
        "h_emu": 420000
      },
      "gap_emu": 90000,
      "min_gap_emu": 50000,
      "wrap_allowed": false,
      "linked_background_by_item": {
        "26": ["27"],
        "28": ["29"],
        "30": ["31"]
      }
    }
  ]
}
```

1차에서 적극 추론하는 layout type:

- `inline_label_group`

1차 fallback:

- `basic_text_area`
- `unknown`

운영 품질 기준:

- editable slot 이 `unknown` 인 경우에는 strict validator 에서 fail 대상으로 본다.
- 다만 1차 rollout 에서는 자동 추론 coverage 를 고려해 warning 모드와 strict 모드를 분리할 수 있다.
- `basic_text_area` 는 unknown 이 아니라 안전 fallback layout type 으로 본다.

## 5. Shape 관계 추론

### 5.1 `item_background`

`item_background` 는 text slot 하나와 강하게 1:1 로 연결되는 작은 배경 도형이다. chip/label 의 배경 상자가 여기에 해당한다.

```text
┌────────────┐
│ OpenAI API │
└────────────┘

1 text slot + 1 small background
```

추론 기준:

- 텍스트 없는 shape
- text slot bbox 를 감싸거나 높은 비율로 겹침
- text slot 보다 약간 큼
- 중심점이 가까움
- 너무 큰 카드/슬라이드 배경이 아님
- 같은 배경 후보가 여러 slot 과 겹치지 않음

`resize_linked_shape` 는 1:1 `item_background` 에만 적용한다.

### 5.2 `container_shape`

하나의 상자 안에 여러 text bbox 가 들어가는 경우는 정상이다. 이때 상자는 개별 slot 의 linked background 가 아니라 `container_shape` 로 본다.

```text
┌──────────────────────────────┐
│ 문제 상황                     │
│ API 비용이 일 10만원 초과...   │
│ 60% 절감                      │
└──────────────────────────────┘

1 container + N text slots
```

1차 정책:

- `container_shape` 는 감지한다.
- 개별 text slot resize 대상에 넣지 않는다.
- overflow 검사에서 사용 가능한 영역을 판단하는 참고 정보로만 사용한다.
- container 내부 재배치는 후속으로 둔다.

## 6. Layout Actions

메인 백엔드 계약을 변경하지 않기 위해 text fills 와 layout actions 를 분리한다.

```text
fills:
{
  "26": {
    "action": "text",
    "text": "OpenAI API"
  }
}

layout_actions:
[
  {
    "action": "resize_linked_shape",
    "shape_id": "26",
    "linked_shape_ids": ["27"],
    "w_emu": 920000
  },
  {
    "action": "relayout_row",
    "group_id": "auto_inline_group_1"
  }
]
```

규칙:

- `currentFills` 에는 `layout_actions` 를 넣지 않는다.
- `layout_actions` 는 워커 내부에서만 사용한다.
- 결과는 OOXML 과 최종 PPTX/PDF/preview 에 반영된다.
- 메인에는 기존 `currentFills` 와 `gcsPreviewKey` 만 보낸다.

`SlideEditor` 확장:

- 기존 `apply_fills()` 는 text/remove/chart 중심으로 유지한다.
- 별도 `apply_layout_actions()` 를 추가한다.
- layout actions 를 먼저 적용하고, 그 다음 text fills 를 적용한다.

1차 우선 action:

- `resize_shape`
- `resize_linked_shape`
- `relayout_row`
- `set_text_color` 또는 text replace 시 marker color 대체

후속 action:

- 자유로운 `move_shape`
- `set_text_box`
- `set_body_margin`
- `set_line_spacing`
- container 내부 재배치

LLM 은 layout action 을 직접 생성하지 않는다. LLM 은 content/fill 후보를 만들고, 엔진이 slot 계약과 layout group 을 기준으로 action 을 계산한다.

## 7. Fitting 전략

### 7.1 LLM 생성 전 capacity hint

LLM 에게는 단순 shape 목록이 아니라 capacity hint 를 함께 제공한다.

```json
{
  "shape_id": "26",
  "placeholder_text": "사용 기술",
  "example_text": "OpenAI API",
  "example_line_count": 1,
  "max_lines": 1,
  "nowrap": true,
  "length_hint": "짧은 기술명 또는 도구명. 예시와 비슷한 길이 권장"
}
```

목표:

- 처음부터 slot 에 맞는 길이의 텍스트를 생성한다.
- 후처리에서 무리하게 줄이거나 요약하는 상황을 줄인다.
- `placeholder_text` 를 content hint 로 적극 활용한다.

### 7.2 Heuristic text measurement

1차에서는 실제 font metric 기반 측정 대신 heuristic 폭 계산으로 시작한다.

예상 규칙:

```text
한글/한자: font_size_pt * 1.0
대문자:    font_size_pt * 0.65
소문자:    font_size_pt * 0.55
숫자:      font_size_pt * 0.55
공백:      font_size_pt * 0.33
기호:      font_size_pt * 0.45
```

계산 후 10~15% 안전 여유를 둔다.

```text
required_width =
  estimated_text_width
  + left_padding
  + right_padding

required_width *= 1.12
```

실제 렌더 결과의 잔여 clipping/overlap 은 기존 Visual QA 가 최종 감지한다.

### 7.3 `inline_label_group` fallback

`inline_label_group` 은 font shrink 보다 resize/relayout 을 우선한다.

우선순위:

1. LLM 생성 전 짧은 label 로 유도
2. item text box 와 `item_background` width 조정
3. 같은 row 의 다음 item 을 오른쪽으로 재배치
4. gap 을 최소값까지 줄임
5. 짧은 대체 텍스트 또는 약칭 요청
6. 실패 처리

기본적으로 wrap 은 허용하지 않는다. `wrap_allowed` 는 후속 확장으로 둔다.

### 7.4 `basic_text_area` fallback

`basic_text_area` 는 일반 텍스트 영역에 대한 안전 fallback 이다.

우선순위:

1. LLM 생성 전 placeholder/example 기반으로 짧게 유도
2. max lines 검사
3. 요약
4. `min_font_pt` 까지만 font shrink
5. 실패 처리

실패를 숨기기 위해 8pt 이하로 줄이는 방식은 금지한다.

## 8. Visual QA 전략

1차에서는 QA 구조를 크게 바꾸지 않는다.

1차 역할:

- 렌더 이미지 기준으로 overflow, clipping, overlap, placeholder 잔존을 감지한다.
- 기존 fix-and-verify 흐름을 유지한다.
- geometry issue 를 완전히 해결하지 못할 수 있다.
- QA 실패 시 별도 layout fitting 재실행은 하지 않는다.

2차 개선:

- QA issue 에 `shape_id`, `group_id`, `layout_type`, `severity`, `retryable` 를 포함한다.
- QA LLM 이 직접 좌표/action 을 만들지 않는다.
- QA 는 `suggested_remedy` 만 구조화한다.
- 엔진이 해당 group 의 fit policy 를 재실행한다.

2차 예시:

```json
{
  "issues": [
    {
      "code": "overlap",
      "group_id": "auto_inline_group_1",
      "suggested_remedy": "rerun_fit_policy",
      "retryable": true
    }
  ]
}
```

## 9. 메인 백엔드 계약

1차에서는 메인 백엔드 계약을 변경하지 않는다.

메인으로 보내는 기존 계약:

- `slidePlan`
- `currentFills`
- `gcsPreviewKey`
- slide/job event
- status transition

워커 내부에서만 사용하는 정보:

- `layout_type`
- `layout_groups`
- `fit_policy`
- `linked_background_by_item`
- `layout_actions`
- `reference.json`
- heuristic fit report

이유:

- 메인 DTO 수정 범위를 줄인다.
- 프론트/SSE 계약 영향을 없앤다.
- 이전처럼 예상하지 않은 callback field 때문에 400 이 나는 위험을 줄인다.
- geometry 결과는 최종 PPTX/PDF/preview 에 이미 반영된다.

재생성 흐름에서 geometry 를 재현해야 할 때는 DB 의 `currentFills` 만 보지 않고 GCS 의 `current.pptx` 를 기반으로 편집한다. 따라서 1차에서는 geometry state 를 메인 DB 에 별도 저장하지 않는다.

## 10. Structured Logging

워커 로컬 파일은 Cloud Run 인스턴스 종료 후 디버깅 자산으로 기대하기 어렵다. 기본은 structured log 로 남긴다.

예시:

```text
[fit] job=... slide=1 group=auto_inline_group_1 layout=inline_label_group result=ok
[fit] job=... slide=1 shape=26 old_w=670560 new_w=920000 overlap=false
[fit] job=... slide=1 group=auto_inline_group_1 result=failed reason=row_overflow fallback=abbreviate_text
```

GCS debug artifact 는 기본 비활성이다. 필요하면 환경변수로 켜는 선택 기능으로 둔다.

```text
PPTX_DEBUG_ARTIFACTS=true
```

옵션 활성화 시 후보:

- `jobs/{job_id}/debug/layout-actions.json`
- `jobs/{job_id}/debug/fit-report.json`

## 11. 구현 작업

### 11.1 템플릿 컴파일러 추가

작업:

- `scripts/templates/compile_template.py` 추가
- 짝수/홀수 slide pair 인식
- `schema_version: 2` 포함
- 기본 실행 시 템플릿 디렉토리의 `meta.json` / `reference.json` 갱신
- 비교/검수용 `--out` 지원
- CI 검증용 `--check` 지원
- 기존 `meta.json` / `reference.json` 수동 필드 보존 없음
- deterministic JSON 출력
- `--check` 는 JSON normalize 후 비교
- 계약 위반은 fail, 품질 위험은 warning 으로 분리
- strict mode 에서 일부 warning 을 fail 로 승격
- `compile_template.py --strict` 지원
- `validate_template.py --strict` 지원
- runtime 대상 슬라이드와 예시 슬라이드 분리 기록
- 유형 슬라이드의 `#FF0000` editable marker 추출
- 예시 슬라이드를 위치/크기 기반으로 매칭
- `reference.json` 생성
- `reference.json` 에도 `schema_version: 2` 포함
- 기존 `meta.json` 에 slot/layout 계약 확장

1차에서 하지 않는 것:

- `runtime_template.pptx` 물리 생성
- theme color 해석
- mixed color run-level replacement
- 기존 meta schema 와의 backward compatibility

완료 기준:

- `schema_version` 이 없거나 2가 아니면 런타임이 fail fast 한다.
- `reference.json` 은 v2 구조로 생성되지만, 런타임 필수 입력은 아니다.
- 컴파일러 기본 실행으로 `meta.json` / `reference.json` 이 재생성된다.
- `--check` 는 현재 산출물이 최신이 아니면 실패한다.
- 기존 산출물의 수동 필드는 보존되지 않는다.
- 빨간 marker 만 editable slot 으로 노출된다.
- non-red 텍스트는 fill 대상에서 제외된다.
- 예시 슬라이드의 텍스트/줄수/글자수/output color 가 reference 로 추출된다.
- runtime 후보에 예시 슬라이드가 포함되지 않는다.

### 11.2 Slot 추출과 layout group 추론

작업:

- `SlideEditor.extract_slots()` 또는 template compiler 에서 텍스트 색상 추출
- `marker_color`, `placeholder_text`, `example_text`, `output_text_color`, `max_lines`, `nowrap` 포함
- `inline_label_group` 강한 패턴 추론
- `item_background` 와 `container_shape` 구분
- ambiguous case 는 억지 분류하지 않고 warning/fallback 처리

완료 기준:

- 첫 슬라이드 하단 chip 이 `inline_label_group` 으로 묶인다.
- chip 텍스트 slot 이 1:1 `item_background` 와 연결된다.
- 여러 text slot 을 담는 큰 상자는 `container_shape` 로 분류되고 개별 resize 대상에서 제외된다.

### 11.3 Deterministic text fit preflight 추가

작업:

- LLM 생성 전에 slot capacity/length hint 제공
- LLM 생성 후 heuristic text measurement 실행
- `nowrap` 위반 감지
- `min_font_pt` 이하 축소 금지
- `inline_label_group` row overlap 사전 검사
- fallback 순서에 따라 layout action 또는 text 재요청 결정

완료 기준:

- `OpenAI API` 같은 tag 가 임의 줄바꿈되지 않는다.
- chip text box 와 background width 가 함께 조정된다.
- row 안의 chip 들이 겹치지 않게 재배치된다.
- row overflow 는 렌더 전에 실패 또는 약칭 fallback 으로 분류된다.

### 11.4 Layout action 적용 구현

작업:

- `SlideEditor.apply_layout_actions()` 추가
- text fills 와 layout actions 분리
- `resize_shape`
- `resize_linked_shape`
- `relayout_row`
- marker color 를 `output_text_color` 로 대체하는 text style 적용

완료 기준:

- layout action 은 `currentFills` 에 섞이지 않는다.
- layout action 결과는 OOXML 에 반영된다.
- 메인 callback payload 는 기존 계약을 유지한다.

### 11.5 Template validator 강화

작업:

- 유형 슬라이드의 editable marker 가 정확히 `#FF0000` 인지 검사
- 빨간 marker shape 안에 non-red run 이 섞이면 fail
- editable marker 가 너무 좁고 fallback 도 없으면 warning/fail
- `inline_label_group` 후보가 linked background 를 찾지 못하면 warning/fail
- placeholder 텍스트가 결과 slot 으로 남을 위험 검사
- 예시 슬라이드가 runtime 후보에 포함되면 fail
- strict mode 에서 editable unknown layout fail

완료 기준:

- 템플릿 등록 시점에 구조적 위험을 발견한다.
- validator 는 "통과했지만 결과가 심하게 깨지는" 상황을 줄인다.

### 11.6 Visual QA 2차 개선

1차 범위가 아니다. 별도 phase 로 진행한다.

작업 후보:

- QA issue schema 에 `shape_id`, `group_id`, `layout_type`, `suggested_remedy` 추가
- QA fix validator 와 generation fill validator 통합
- QA 가 직접 geometry action 을 만들지 못하도록 제한
- QA issue 기반으로 deterministic fit policy 재실행

## 12. 단계별 실행 계획

### Phase A - 계약 정리 및 컴파일러

- 짝수/홀수 pair convention 문서화
- `#FF0000` marker convention 문서화
- `compile_template.py` 초안 구현
- `schema_version: 2` fail-fast 정책 구현
- `meta.json` / `reference.json` 산출물 생성
- runtime 대상 슬라이드와 예시 슬라이드 분리 기록

### Phase B - Slot / Group / Validator

- 빨간 marker 기반 editable slot 추출
- 예시 shape 위치/크기 매칭 구현
- `inline_label_group` 추론 구현
- `item_background` / `container_shape` 추론 구현
- template validator 강화

### Phase C - Fitting / Layout Actions

- heuristic text measurement 구현
- LLM capacity hint prompt 적용
- `layout_actions` 계산
- `SlideEditor.apply_layout_actions()` 구현
- `resize_linked_shape`, `relayout_row` 우선 구현
- 첫 슬라이드 하단 chip 문제를 acceptance fixture 로 고정
- 정량 성과 수치 slot 을 marker/output color fixture 로 고정

### Phase D - QA 2차

- QA issue 구조화
- suggested remedy 도입
- QA fix validator 통합
- geometry remedy 는 deterministic engine 에 위임

## 13. Acceptance Criteria

1차 acceptance fixture:

1. 첫 슬라이드 하단 기술 스택 chip
   - `inline_label_group` 감지
   - `item_background` 1:1 연결
   - `OpenAI API` 같은 공백 포함 tag 한 줄 유지
   - chip text box 와 background width 조정
   - 같은 row 의 chip overlap 없음
   - `layout_actions` 가 `currentFills` 에 섞이지 않음
2. 정량 성과 수치 slot
   - `#FF0000` marker 감지
   - `placeholder_text` 사용
   - 예시의 `150%` 같은 성과 수치 매칭
   - `output_text_color` 추출
   - 최종 결과에서 marker red 제거

1차 최소 완료 기준:

- 유형 슬라이드의 `#FF0000` 텍스트만 포트폴리오 내용으로 교체된다.
- non-red 양식 문구는 결과물에서 원문 그대로 유지된다.
- 예시 슬라이드는 출력 후보에 포함되지 않는다.
- 빨간 marker 색상은 최종 결과에 남지 않고, 예시의 대응 텍스트 색상으로 대체된다.
- `placeholder_text` 가 LLM prompt 에 힌트로 전달된다.
- 첫 슬라이드 하단 chip 은 텍스트가 한 줄로 유지된다.
- 첫 슬라이드 하단 chip 의 배경 박스가 텍스트 폭에 맞게 조정된다.
- 같은 row 의 chip 들이 겹치지 않는다.
- `OpenAI API` 같은 공백 포함 tag 가 임의 줄바꿈되지 않는다.
- layout action 이 `currentFills` 에 섞이지 않는다.
- 메인 백엔드 callback 계약이 변경되지 않는다.
- 템플릿 validator 가 marker/예시/runtime 후보 위반을 발견한다.
- Visual QA 는 기존 방식으로 최종 렌더의 placeholder, clipping, overlap 을 감지한다.

후속 완료 기준:

- `title_block`, `metric_block`, `body_text_area` 자동 추론이 추가된다.
- container 내부 재배치가 가능해진다.
- QA 가 remedy 를 구조화하고 deterministic fit policy 재실행을 요청할 수 있다.
- 필요 시 `runtime_template.pptx` 를 물리 생성한다.

## 14. 문서 업데이트 필요 항목

이 로드맵 구현 시 다음 문서를 함께 갱신해야 한다.

- `template-system.md`
  - 짝수/홀수 pair convention
  - `#FF0000` editable marker convention
  - 예시 슬라이드 reference 활용 방식
  - `meta.json` / `reference.json` slot 계약 설명
- `ooxml-editing.md`
  - `apply_layout_actions()`
  - resize/relayout action
  - marker color 대체
  - item background/container 처리 규칙
- `qa-and-guardrails.md`
  - 1차 QA 유지 범위
  - 2차 suggested remedy 구조
  - deterministic preflight 와 QA 책임 분리
- `pptx-gen-plan-v6.md`
  - 오래된 `/api/internal` 경로 잔재 정리
  - width/height/byteSize callback 잔재 정리
  - `currentFills` 와 internal `layout_actions` 분리 명시

## 15. 설계상 주의사항

### 15.1 Marker 와 output style 을 혼동하지 않는다

유형 슬라이드의 `#FF0000` 은 최종 색상이 아니라 editable marker 다. 최종 텍스트 색상은 유형 슬라이드의 marker 색상을 그대로 쓰지 않는다. 1차에서는 예시 슬라이드의 대응 텍스트 색상으로 marker 색상을 대체한다.

### 15.2 예시 슬라이드는 정답이 아니다

예시 슬라이드는 레이아웃 의도를 보여주는 reference 다. LLM 이 예시 텍스트를 그대로 복사하지 않도록, 프롬프트에는 형식/길이/밀도 참고로만 제공한다.

### 15.3 자동 추론은 확신이 있을 때만 한다

`inline_label_group` 과 `item_background` 는 강한 패턴일 때만 자동 추론한다. 애매한 경우 억지 분류하지 않고 `basic_text_area` 또는 warning/fallback 으로 둔다.

### 15.4 하드코딩 금지

`origin` 템플릿의 특정 shape id 나 slide index 에 의존하는 로직은 피한다. `origin` 은 첫 fixture 로 사용하되, 엔진은 다음 신호를 조합해 범용적으로 동작해야 한다.

1. `#FF0000` marker convention
2. 유형/예시 slide pair
3. 위치/크기 기반 example shape 매칭
4. 좌표/크기/겹침 기반 item background 추론
5. 반복 구조 기반 inline label group 추론
6. 안전 fallback
