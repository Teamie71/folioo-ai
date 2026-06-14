# FOLIOO 시각화 — 기술 스택 및 OOXML 편집 방식

> 이 문서는 `pptx-gen-plan-v6.md` 의 **§4** 를 분리한 것이다.
> 절 번호(§4.x)는 원본 문서와의 교차참조 유지를 위해 그대로 둔다.
> 템플릿 시스템은 `template-system.md`(§3) 참조.

## 4. 기술 스택 및 OOXML 편집 방식

### 4.1 Anthropic PPTX Skill 기반 도구 체인

Anthropic의 PPTX 스킬 문서에서 제공하는 도구 체인을 기반으로 OOXML 직접 편집 방식을 채택한다.

| 도구 | 용도 |
|---|---|
| `unpack.py` | PPTX ZIP 해제 + XML pretty-print + 스마트따옴표 이스케이프 |
| `clean.py` | 고아 슬라이드/미디어/rels/ContentTypes 정리 |
| `pack.py` | XML condense + ZIP 패키징 + 원본 대비 검증/자동 수리 |
| `validate.py` | OOXML 스키마 검증 (웰포름드, 네임스페이스, ID 유일성, rels 참조, XSD) |
| `thumbnail.py` | 슬라이드 그리드 썸네일 생성 |
| `soffice.py` | LibreOffice 헤드리스 PDF 변환 (샌드박스 UNIX 소켓 우회 포함) |
| `markitdown` | PPTX에서 텍스트 구조 추출 |

### 4.2 왜 python-pptx가 아닌 OOXML 직접 편집인가

```
python-pptx로 생성하는 방식의 문제:
- 템플릿 디자이너가 만든 미세한 스타일 (그림자, 그라데이션, 간격, 정렬 등)을
  JSON으로 100% 표현 불가
- 코드로 재현하면 "비슷하지만 미묘하게 다른" 결과물 (70~80% 유사도)

OOXML 직접 편집 방식:
- 실제 PPTX 템플릿의 XML에서 텍스트/데이터만 교체
- 그림자, 그라데이션, 간격 등 원본 서식 완전 보존 (95~99% 유사도)
- 디자이너가 만든 품질 그대로 유지
```

### 4.3 XML 편집 규칙 (DrawingML)

| 규칙 | 설명 |
|---|---|
| XML 파서 | `defusedxml.minidom` 사용. `xml.etree.ElementTree`는 네임스페이스 손상 우려로 비권장 |
| 텍스트 치환 | `sed`/일괄 치환 금지 → 명시적 노드 조작 |
| 제목/라벨 | `b="1"` 등 굵게 처리 |
| 불릿 | 유니코드 불릿 문자 `•` 금지 → 목록 마크업 사용 |
| 항목 분리 | 한 `<a:p>`에 몰지 말고 문단별 분리 |
| 공백 보존 | `xml:space="preserve"` |
| 스마트 따옴표 | XML 엔티티 참조로 처리 |
| 항목 수 불일치 | 텍스트만 비우지 말고 도형·이미지 등 Slot 전체 제거 |
| 차트 | `<p:graphicFrame>` 의 차트 파트(`chartN.xml`) 캐시(`numCache`/`strCache`/`ptCount`/`c:f`)만 갱신 — 타입 고정·개수 가변, 임베디드 `.xlsx` 미동기 (§4.4.5, ADR-0003) |

### 4.4 슬라이드 XML 편집 구현

> 식별자는 **`cNvPr/@id`** (PowerPoint 가 자동 부여하는 정수 ID) 를 사용한다.
> 디자이너가 부여하는 `cNvPr/@name` 에는 의존하지 않는다 — `template-system.md` §3.7 자동 Slot 인식 참조.
> 슬라이드 편집은 세 책임으로 나뉜다:
>
> 1. **`extract_slots()`** — 슬라이드 XML 을 스캔해 LLM 에 줄 Slot 디스크립터 생성
> 2. **`apply_layout_actions()`** — 워커 내부 geometry action 을 OOXML 에 먼저 적용
> 3. **`apply_fills()`** — `currentFills` 의 text/remove/chart fill 을 XML 에 적용
>
> 텍스트 도형(`<p:sp>`), 그림(`<p:pic>`), 차트(`<p:graphicFrame>`)는 모두
> `cNvPr/@id` 로 식별한다. 차트 Slot 처리 규칙은 §4.4.5 참조 (ADR-0003).

#### 4.4.1 `layout_actions` 적용

`layout_actions` 는 LLM 이 직접 만들지 않는다. LLM fill 결정 후 deterministic preflight 가
v2 slot metadata(`layout_groups`, `fit_policy`, `item_background`)를 기준으로 계산하고,
워커 내부에서 `apply_layout_actions()` 에 전달한다. 이 값은 Main callback payload 나
DB 의 `currentFills` 에 넣지 않는다.

| action | 필수/주요 payload | 처리 규칙 |
|---|---|---|
| `resize_shape` | `shape_id`, `x_emu`/`y_emu`/`w_emu`/`h_emu` 중 하나 이상 | 대상 shape 의 OOXML `xfrm/off/ext` 값을 직접 변경 |
| `resize_linked_shape` | `shape_id`, `linked_shape_ids`, text geometry, linked geometry | text shape 과 1:1 `item_background` shape 의 geometry 를 함께 변경 |
| `relayout_row` | `group_id`, `items[]`, `min_gap_emu` | 같은 row 의 text item 과 linked background x 좌표를 함께 재배치 |

적용 순서:

1. `layout_actions` 로 geometry 를 먼저 확정한다.
2. 이후 `apply_fills()` 로 text/remove/chart fill 을 적용한다.
3. pack/validate/PDF/preview 는 geometry 가 반영된 slide XML 을 기준으로 수행한다.

검증 규칙:

- 알 수 없는 action, 존재하지 않는 `shape_id`, 잘못된 geometry payload 는 `ValueError` 로 실패한다.
- `w_emu`, `h_emu` 는 양수여야 한다. `x_emu`, `y_emu` 는 OOXML 좌표이므로 음수 가능성을 열어둔다.
- `relayout_row` 는 text box 뿐 아니라 linked background 를 포함한 visible bounds 기준으로
  순서와 `min_gap_emu` 를 검증한다.
- action 실패는 slide-level 오류로 격리하고, 잘못된 geometry 가 success 경로로 올라가지 않게 한다.

#### 4.4.2 marker color replacement

runtime slide 의 `#FF0000` 은 editable marker 이지 최종 출력 색상이 아니다. `apply_fills()` 는
text fill 을 적용할 때 slot metadata 또는 fill payload 의 `marker_color` 와 `output_text_color` 를
사용한다.

| 입력 | 처리 |
|---|---|
| `output_text_color` 있음 | 기준 run 의 색상을 해당 RGB 로 교체 |
| `marker_color == "#FF0000"` 이고 `output_text_color` 없음 | fallback `#000000` 적용 후 warning 반환 |
| 기준 run 이 이미 `#FF0000` 이고 metadata 없음 | marker 잔존 방지를 위해 fallback 적용 |

`marker_color` 와 `output_text_color` 는 `currentFills` 의 필수 필드가 아니다. 워커는
`slot_metadata` 를 `shape_id` 로 조회해 fill 보다 낮은 우선순위로 병합한다. 따라서 Main backend 는
기존 `currentFills` 계약을 변경하지 않아도 된다.

#### 4.4.3 `item_background` / `container_shape` 처리

`item_background` 과 `container_shape` 는 compiler 가 `reference.json.shape_inferences` 와
`meta.json.slots[]` / `layout_groups[]` 에 남기는 구조 정보다. 이 정보는 fill payload 가 아니라
geometry 계산의 입력이다.

| 관계 | 의미 | OOXML 적용 규칙 |
|---|---|---|
| `item_background` | 하나의 text slot 을 감싸는 작은 1:1 배경 shape | `resize_linked_shape` 의 `linked_shape_ids` 로만 함께 resize |
| `container_shape` | 여러 text slot 을 담는 큰 카드/섹션 배경 shape | 개별 slot 의 linked resize 대상에서 제외 |

`item_background.resize_linked` 가 명확한 경우에만 linked resize 를 수행한다. 같은 background 가
여러 slot 과 겹치거나 큰 카드 배경으로 보이면 `container_shape` 또는 warning/fallback 으로 남기며,
`resize_linked_shape` 대상에 넣지 않는다.

#### 4.4.4 `currentFills` 와 `layout_actions` 책임 경계

`currentFills` 는 Main backend 와 callback payload 에 노출되는 기존 상태이다. 값은 평평한
`shape_id -> fill` 맵이고, `action` 은 `text`, `remove`, `chart` 로 제한한다.

`currentFills` 에 넣는 것:

- text fill 의 `text`, `font_size_override`, `is_title`
- remove fill 의 `action: "remove"`
- chart fill 의 `data`, `chart_type` 같은 차트 교체 입력

`currentFills` 에 넣지 않는 것:

- `layout_actions`
- `x_emu`, `y_emu`, `w_emu`, `h_emu` 같은 geometry 변경 명령
- `linked_shape_ids`, `item_background`, `container_shape`
- `layout_groups`, `fit_policy` 같은 compiler/preflight 내부 metadata

geometry 는 최종 PPTX/PDF/preview 에 반영되지만 Main 이 저장하는 fill 계약에는 섞지 않는다.
재생성 흐름에서 geometry 상태가 필요하면 DB 의 `currentFills` 만 보지 않고 GCS 의 현재 PPTX 를
기준으로 다시 편집한다.

#### 4.4.5 차트 Slot 처리 (네이티브 캐시 편집 — ADR-0003)

차트는 `<p:sp>` 가 아니라 `<p:graphicFrame>` 이고, 내부는 별도 차트 파트
(`/ppt/charts/chartN.xml`) → 임베디드 엑셀(`.xlsx`) 로 이어진다. 따라서
`extract_slots()` / `apply_fills()` 는 `<p:sp>` 와 함께 `<p:graphicFrame>` 도
순회하고, graphicFrame 의 rels 로 차트 파트를 찾아 들어간다.

**fill 표현 — 텍스트와 동일하게 shape_id(=graphicFrame 의 `cNvPr/@id`) 키:**

```json
"<graphicFrame_cNvPr_id>": {
  "action": "chart",
  "chart_type": "bar",          // 읽기 전용 참고값 — 타입은 바꾸지 않는다
  "data": {
    "categories": ["전환율", "이탈률"],
    "series": [{ "name": "개선 후", "values": [148, 32] }]
  }
}
```

(이전 설계의 최상위 별도 `chart` 키는 폐기 — 차트도 `fills` 안의 한 도형 엔트리다.)

**편집 규칙:**

| 항목 | 규칙 |
|---|---|
| 차트 타입 | 템플릿 차트 그대로 고정. `bar→pie` 같은 타입 변경 없음 |
| 시리즈/카테고리 개수 | 콘텐츠에 맞춰 가변 — `chartN.xml` 의 `c:ser`/`c:pt` 추가·삭제 |
| 갱신 대상 | `<c:numCache>`/`<c:strCache>` 값 + `<c:ptCount>` + 수식 범위 `<c:f>` 문자열 (세 곳 일관) |
| 임베디드 `.xlsx` | **MVP 미동기.** 렌더·표시는 캐시가 결정하므로 정확하나, PowerPoint "데이터 편집" 시 원본 샘플 데이터가 보인다 (한계 — 승급은 §17) |
| 데이터 초과 | 카테고리가 차트 Slot 수보다 많으면 LLM 이 요약/절삭해 맞춘다 (§4.3 항목 수 불일치와 동일 철학) |
| 텍스트 전용 필드 | `font_size_override`·`is_title` 는 차트 fill 에 적용되지 않는다 |
