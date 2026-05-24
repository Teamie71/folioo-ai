# 템플릿 등록 파이프라인 스크립트 (오프라인 운영자/CI)

## Purpose
디자이너가 완성한 `template.pptx` 로부터 `meta.json` 초안을 반자동 생성하고 그 무결성을 검증하는 오프라인 운영자/CI 스크립트를 구축해, Source Slide 풀을 런타임 시각화 워커가 신뢰할 수 있는 형태로 등록한다.

## Requirements
- `scripts/templates/build_meta.py` 가 `template.pptx` 를 soffice→PDF→pdftoppm 으로 슬라이드별 JPG + 그리드 `thumbnail.jpg` 로 만들고, markitdown 으로 임시 텍스트를 추출한 뒤, (썸네일+텍스트)를 LLM 에 입력해 Source Slide 별 `{category, description, best_for}` 초안과 같은 카테고리 내 알파벳순 `id` 를 자동 부여한 `meta.json` 초안을 작성한다.
- `scripts/templates/validate_template.py` 가 `meta.json` 필수 필드 스키마, `category` 가 `templates/_schema/categories.json` Enum 안에 있는지(unknown 이면 실패), `slide_index` 가 `0..N-1` 연속이며 PPTX 슬라이드 수와 일치하는지, 템플릿 내 `id` 중복이 없는지를 검증하고 CI 에서도 실행된다.
- `templates/_schema/categories.json` 을 §3.3 표준 카테고리 Enum(cover/toc/overview/problem/process/outcome/chart/visual/text/closing)의 단일 소스 오브 트루스로 둔다.
- 의미 필드(`category`/`description`/`best_for`/`id`)는 LLM 초안 후 운영자가 검토·확정하는 반자동 산출물로 다루고, `slide_index`/`template_file` 은 자동 생성 후 운영자가 손대지 않는다.

## Approach
런타임 워커(`apps/pptx-worker/`)와 분리된 오프라인 운영자/CI 도구로, soffice/pdftoppm/markitdown 도구 체인은 spec 04 와 공유하되 런타임 경로에는 두지 않는다(template-system.md §3.5). `build_meta.py` 는 §3.5.1 의 [1]+[2] 단계(자동 추출 + LLM 초안)를 한 번에 수행하고, 운영자 검토([3])와 `validate_template.py` 검증([4])을 거쳐 GCS 업로드([5])로 이어진다. 메타 작성 LLM 호출은 빌드 단계라 런타임 사용자 비용에 영향이 없다(§3.5.4). 카테고리 분포 권장 범위는 경고만 내고 실패시키지 않는다.

## Verification
- 샘플 `template.pptx` 로 `build_meta.py` 를 실행하면 슬라이드별 JPG·그리드 thumbnail·임시 텍스트가 생성되고, Source Slide 별 `{category, description, best_for}` 와 카테고리별 알파벳순 `id` 가 채워진 `meta.json` 초안이 디스크에 작성되는지 확인한다.
- `validate_template.py` 가 필수 필드 누락·Enum 밖 category(unknown 포함)·`slide_index` 불연속·PPTX 슬라이드 수 불일치·중복 `id` 를 각각 실패로 잡아내는지 검증한다.
- `category` 가 `templates/_schema/categories.json` 의 값과 일치할 때만 검증을 통과하고, Enum 을 확장하지 않은 신규 값은 실패하는지 확인한다.
