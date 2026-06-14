# PPTX 템플릿 품질 문서 정합성 업데이트

## Purpose
템플릿 품질 개선 로드맵의 v2 metadata, marker convention, layout action 책임 분리를 기존 아키텍처 문서에 반영해 구현자와 운영자가 같은 계약을 기준으로 작업하게 만든다.

## Requirements
- `template-system.md` 에 짝수/홀수 slide pair, `#FF0000` editable marker, 예시 슬라이드 reference 활용, `meta.json`/`reference.json` v2 slot 계약을 반영한다.
- `ooxml-editing.md` 에 `apply_layout_actions()`, resize/relayout action, marker color 대체, `item_background` 와 `container_shape` 처리 규칙을 추가한다.
- `qa-and-guardrails.md` 에 1차 QA 유지 범위, deterministic preflight 와 Visual QA 책임 분리, 2차 suggested remedy 방향을 명시한다.
- `pptx-gen-plan-v6.md` 의 오래된 `/api/internal`, width/height/byteSize callback 잔재를 정리하고 `currentFills` 와 internal `layout_actions` 분리를 설명한다.
- 문서 간 용어는 `schema_version`, `placeholder_text`, `layout_groups`, `fit_policy`, `layout_actions` 를 기준으로 통일한다.

## Approach
로드맵 문서를 source of truth 로 삼아 기존 문서의 상충되는 설명과 오래된 callback/API 흔적을 정리한다. 구현 세부 코드나 긴 예시는 각 문서의 기존 수준에 맞춰 최소화하고, 계약과 책임 경계가 드러나도록 업데이트한다. Visual QA 2차 개선은 1차 범위와 분리해 후속 방향으로만 기록한다. 문서 변경은 구현 스펙과 같은 용어를 사용해 이후 task 분해와 코드 리뷰에서 추적 가능하게 만든다.

## Verification
- 네 문서가 모두 v2 metadata 와 `#FF0000` marker convention 을 같은 의미로 설명하는지 확인한다.
- `currentFills` 에 `layout_actions` 를 넣지 않는다는 계약이 `pptx-gen-plan-v6.md` 와 OOXML 문서에 모두 반영되는지 검증한다.
- QA 문서가 1차에서 geometry action 을 직접 생성하지 않는다고 명시하는지 확인한다.
- `/api/internal`, width/height/byteSize callback 같은 오래된 계약 잔재가 제거되거나 현재 계약으로 대체되었는지 확인한다.
- 문서 내 `schema_version`, `layout_group`, `fit_policy` 용어가 상충 없이 사용되는지 검증한다.
