# 차트 슬라이드는 네이티브 OOXML 캐시(chartN.xml) 직접 편집으로 채운다

Status: accepted

PPTX 차트는 일반 텍스트 도형(`<p:sp>`)이 아니라 `<p:graphicFrame>` → 별도 차트 파트(`/ppt/charts/chartN.xml`) → 임베디드 엑셀 워크북(`.xlsx`) 의 3겹 구조다. 시각화 워커의 콘텐츠 편집 원칙(LLM 은 `fills` 데이터만 내고 결정적 `apply_fills` 가 XML 에 적용 — v6 §4 / §15)을 차트에도 일관되게 적용하려면, 차트를 **어느 깊이까지** 편집할지 못박아야 한다. python-pptx 차트 API 와 차트를 이미지로 렌더하는 방식도 후보였으나, 전자는 디자이너 서식 보존을 노린 OOXML 직접 편집 원칙(§4.2)과 어긋나고, 후자는 "편집 가능한 네이티브 차트" 라는 산출물 가치를 잃는다.

결정:
- 차트는 **네이티브 차트를 유지**하고, LLM 데이터로 `chartN.xml` 의 **캐시(`<c:numCache>`/`<c:strCache>`)·점 개수(`<c:ptCount>`)·수식 범위(`<c:f>`)를 직접 갱신**한다.
- **차트 타입은 템플릿 차트 그대로 고정**(막대→파이 같은 타입 변경 없음), **시리즈/카테고리 개수는 콘텐츠에 맞춰 가변**(`c:ser`/`c:pt` 추가·삭제).
- **임베디드 엑셀 워크북은 MVP 에서 동기화하지 않는다.** 렌더·표시는 캐시가 결정하므로 정확하지만, PowerPoint "데이터 편집" 을 열면 원본 샘플 데이터가 보인다 — 의도된 한계로 문서에 명시한다.
- 차트는 `fills` 에서 **graphicFrame 의 `cNvPr/@id` 를 key 로, `action: "chart"`** 로 표현한다(최상위 별도 `chart` 키 폐기). `extract_slots`/`apply_fills` 는 `<p:sp>` 와 함께 `<p:graphicFrame>` 도 스캔하도록 확장한다.

## Considered Options

- **A. python-pptx 차트 API** — 서식 보존 목적의 OOXML 직접 편집 원칙(§4.2)과 충돌
- **B. 차트를 이미지(matplotlib 등)로 렌더해 이미지 슬롯에 삽입** — 네이티브/편집 가능 차트 가치 상실, 이미지 생성 경로 추가
- **C. 네이티브 + 캐시만 편집(타입 고정·개수 가변), 워크북 미동기** ← 채택
- **D. 네이티브 + 임베디드 워크북까지 동기** — "데이터 편집" 도 정확하나 xlsx 쓰기·범위 동기화 비용. C→D 는 데이터 계약·사용자 화면 변경 없는 순수 추가라 추후 승급 가능(§17)

## Consequences

- `ooxml-editing.md` §4.4 의 `extract_slots`/`apply_fills` 는 graphicFrame + 차트 파트(rels 경유)까지 다루도록 확장된다 — 규칙은 §4.4.1.
- 캐시와 임베디드 워크북이 어긋난 채로 export 되므로, "PowerPoint 에서 차트 데이터 편집 시 원본 샘플이 보일 수 있음" 을 사용자/문서 한계로 둔다. 정확 편집이 필요해지면 옵션 D 로 승급.
- `font_size_override`·`is_title` 같은 텍스트 전용 필드는 차트 fill 에 적용되지 않는다 — 차트 fill 스키마는 `chart_type`(읽기 전용 참고)·`data`(categories/series)만 갖는다.
