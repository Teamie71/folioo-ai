# PPTX 템플릿 Validator v2 Strict Mode

## Purpose
템플릿 등록과 배포 전에 v2 metadata 계약 위반과 품질 위험을 검출해, placeholder 잔존, marker 오인식, chip 배경 미연결 같은 런타임 품질 문제를 조기에 차단한다.

## Requirements
- `validate_template.py` 는 `template.pptx`, `meta.json`, 선택적 `reference.json` 의 `schema_version: 2` 구조와 runtime/example 슬라이드 분리를 검증한다.
- Runtime 대상 슬라이드 없음, 정확한 `#FF0000` editable marker 없음, mixed-color run, 필수 예시 slide pair 없음, editable slot 예시 매칭 실패, JSON 생성/파싱 실패는 기본 모드에서도 fail 처리한다.
- Editable slot 이 너무 좁음, `inline_label_group` 후보의 linked background 신뢰도 낮음, `output_text_color` fallback 사용, `basic_text_area` fallback, placeholder 잔존 위험은 warning 으로 보고한다.
- `--strict` 에서는 운영 배포 전 품질 게이트로 일부 warning 을 fail 로 승격하고, editable slot 의 `unknown` layout 은 실패로 본다.
- 예시 슬라이드가 runtime 후보에 포함되거나 non-red 텍스트가 editable slot 으로 노출되면 실패한다.

## Approach
Validator 는 compiler 와 같은 추출/추론 결과를 재사용하되, 산출물 작성이 아니라 계약 검증과 리포팅에 집중한다. 기본 모드는 개발 반복을 위해 치명적인 계약 위반만 실패시키고, strict mode 는 배포 전 품질 기준을 적용한다. 결과 메시지는 어떤 slide, shape, group 이 문제인지 식별 가능한 structured output 으로 제공한다. 기존 템플릿 등록 파이프라인의 category/thumbnail 검증은 유지하면서 v2 slot/layout 계약 검증을 추가한다.

## Verification
- `#FF0000` editable marker 가 없는 유형 슬라이드가 기본 모드에서 실패하는지 확인한다.
- 한 text shape 안에 red run 과 non-red run 이 섞이면 실패하는지 검증한다.
- 예시 슬라이드가 runtime 후보에 포함된 metadata 가 실패하는지 확인한다.
- `inline_label_group` 의 linked background 신뢰도가 낮은 경우 기본 모드는 warning, strict mode 는 실패로 처리되는지 검증한다.
- Editable `unknown` layout 이 strict mode 에서 실패하고 기본 모드에서는 warning 으로 보고되는지 확인한다.
