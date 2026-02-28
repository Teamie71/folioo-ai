# YAML 설정 반영 규칙 (호환 우선)

## Portfolio (`features/portfolio/config/portfolio.yaml`)
- `llm.model` → `get_llm(model=...)`
- `llm.temperature` → `get_llm(temperature=...)`
- `llm.max_retries` → 포트폴리오 생성 재시도 횟수
- `sections.*.required` → 출력 필수 섹션 검증 규칙
- `section_mapping` → 포트폴리오 생성 프롬프트의 섹션 매핑 가이드

## Correction (`features/correction/config/correction.yaml`)
- `llm.model`, `llm.temperature` → 첨삭 Generator LLM 초기화
- `validation.max_retries` → 기본 재시도 횟수
- `validation.allow_null_comment_for_keep` → `keep` 라인 `comment` null 허용 여부
- `validation.min_lines_per_field` → 필드별 최소 라인 검증 기준

### 우선순위 (호환)
1. `correction.yaml.validation.max_retries`
2. 없으면 `generator.yaml.generator.max_retries` fallback

> `features/correction/config/generator.yaml`은 호환 목적의 fallback 설정으로 유지되며, 추후 deprecated 대상입니다.

## Interview (`features/interview/config/stages.yaml`)
- `global_config.max_retries_per_question` → 질문 생성 LLM 재시도 횟수
- `global_config.enable_dynamic_followup` → 동적 후속 질문 on/off
- `global_config.context_window_size` → 대화 컨텍스트 윈도우 크기
