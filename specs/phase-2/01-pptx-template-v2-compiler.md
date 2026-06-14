# PPTX 템플릿 v2 컴파일러

## Purpose
`template.pptx` 의 짝수/홀수 슬라이드 convention 과 `#FF0000` marker 를 `schema_version: 2` 메타데이터 계약으로 컴파일해, 런타임 워커가 편집 가능한 slot 과 reference 정보를 결정적으로 신뢰할 수 있게 만든다.

## Requirements
- `scripts/templates/compile_template.py` 는 `templates/{template_id}/template.pptx` 를 읽어 runtime 슬라이드와 예시 슬라이드를 분리하고, 기본 실행 시 같은 디렉토리의 `meta.json` 과 `reference.json` 을 갱신한다.
- `--out`, `--check`, `--strict` 옵션을 지원하고, `--check` 는 JSON parse 후 normalize 한 결과가 현재 산출물과 다르면 실패한다.
- 유형 슬라이드의 정확한 `#FF0000` 텍스트 shape 만 editable slot 으로 등록하고, non-red 텍스트는 fixed/decorative 로 유지하며 mixed-color run 은 계약 위반으로 실패한다.
- 예시 슬라이드는 runtime 후보에서 제외하고, 위치/크기 기반 매칭으로 `example_text`, 줄 수, 글자 수, `output_text_color` 를 `reference.json` 에 추출한다.
- 산출 JSON 은 `schema_version: 2` 를 포함하고 deterministic 하게 기록하며, 기존 수동 필드는 1차에서 보존하지 않는다.

## Approach
컴파일러는 런타임 워커와 분리된 오프라인/CI 도구로 두고, 원본 `template.pptx` 를 source of truth 로 삼는다. 슬라이드 pair, 텍스트 색상, bbox 정보를 추출해 v2 `meta.json` 과 `reference.json` 을 생성하되, `runtime_template.pptx` 물리 파일은 만들지 않는다. 런타임 로더는 `schema_version` 이 없거나 2가 아니면 fail fast 하고, `reference.json` 없이도 `meta.json` 만으로 기본 생성이 가능해야 한다. 계약 위반은 실패로, 품질 위험은 warning 으로 분리해 strict mode 에서만 일부 warning 을 실패로 승격한다.

## Verification
- `docs/ppt-v3.pptx` 기반으로 신규 등록한 템플릿 디렉터리에서 컴파일러를 실행하면 `meta.json` 과 `reference.json` 이 `schema_version: 2` 로 재생성되는지 확인한다.
- `--check` 실행 시 현재 산출물과 normalize 결과가 다르면 실패하고, 동일하면 성공하는지 확인한다.
- 유형 슬라이드의 `#FF0000` marker 만 editable slot 으로 노출되고 non-red 텍스트는 fill 대상에서 제외되는지 검증한다.
- 예시 슬라이드가 runtime 후보에 포함되지 않고 reference 정보만 생성되는지 확인한다.
- `schema_version` 누락 또는 2가 아닌 `meta.json` 로 런타임 로딩이 fail fast 하는지 검증한다.
