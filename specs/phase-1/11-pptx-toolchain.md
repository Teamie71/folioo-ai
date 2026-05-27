# PPTX 도구 체인 (unpack/clean/pack/validate + 미선택 슬라이드 제거)

## Purpose
무상태 작업 디렉터리에서 PPTX 를 해제·고아 정리·재패키징·스키마 검증하고, 미선택 슬라이드를 제거하는 패키지 수준 OOXML 도구 체인 어댑터를 구축한다.

## Requirements
- Anthropic PPTX 스킬 도구 체인(`unpack`/`clean`/`pack`/`validate`)을 래핑하고, 검증 실패 시 `repair()` 후 재검증한다.
- `presentation.xml` 에서 미선택 슬라이드의 `sldId` 를 제거하고 `clean` 으로 고아 파트를 정리한다(Step 2).
- 모든 작업은 `/tmp` 하위 작업 디렉터리에서 수행하고 잔여물을 남기지 않는다.

## Approach
`apps/pptx-worker/features/visualization/pptx/` 에 도구 체인 어댑터(`toolchain.py` 등)를 둔다. 슬라이드 XML 내부 편집(텍스트·차트 교체/제거)은 spec 03 `SlideEditor` 책임이고, 본 레이어는 패키지 수준 unpack/pack/validate 와 슬라이드 단위 제거만 담당한다. `pack` 결과 `.pptx` 는 spec 04 soffice 렌더의 입력이다.

## Verification
- 샘플 template.pptx 를 unpack→미선택 슬라이드 제거→clean→pack 했을 때 검증을 통과하고 선택 슬라이드만 남는지 확인한다.
- `validate` 실패를 유도했을 때 `repair()` 후 재검증이 동작하는지 확인한다.
- 작업 종료 후 `/tmp` 작업 디렉터리가 비워지는지 확인한다.
