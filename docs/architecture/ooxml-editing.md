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
| 항목 수 불일치 | 텍스트만 비우지 말고 도형·이미지 등 슬롯 전체 제거 |
| 차트 | `<p:graphicFrame>` 의 차트 파트(`chartN.xml`) 캐시(`numCache`/`strCache`/`ptCount`/`c:f`)만 갱신 — 타입 고정·개수 가변, 임베디드 `.xlsx` 미동기 (§4.4.1, ADR-0003) |

### 4.4 슬라이드 XML 편집 구현

> 식별자는 **`cNvPr/@id`** (PowerPoint 가 자동 부여하는 정수 ID) 를 사용한다.
> 디자이너가 부여하는 `cNvPr/@name` 에는 의존하지 않는다 — `template-system.md` §3.7 자동 placeholder 인식 참조.
> 슬라이드 편집은 두 단계로 나뉜다:
>
> 1. **`extract_slots()`** — 슬라이드 XML 을 스캔해 LLM 에 줄 슬롯 디스크립터 생성
> 2. **`apply_fills()`** — LLM 응답(shape_id → 콘텐츠) 을 받아 XML 에 적용
>
> 텍스트 도형(`<p:sp>`) 외에 **차트(`<p:graphicFrame>`)** 도 두 함수의 스캔 대상이다.
> 차트 슬롯 처리 규칙은 §4.4.1 참조 (ADR-0003).

```python
from defusedxml.minidom import parse


class SlideEditor:
    """
    DrawingML 규칙을 준수하는 슬라이드 편집기.

    placeholder 사전 명명 불필요 — 도형의 cNvPr/@id 와
    위치·크기·현재 텍스트·폰트 크기로 LLM 이 동적으로 슬롯 역할을 추론한다.
    """

    PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
    DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

    # ─────────────────────────────────────────────────────────────
    # 1) 슬롯 자동 추출 (LLM 입력용)
    # ─────────────────────────────────────────────────────────────
    def extract_slots(self, slide_xml_path: str) -> list[dict]:
        """
        슬라이드 XML 에서 텍스트가 들어가는 도형(<p:sp>)을 모두 스캔하여
        LLM 에 줄 슬롯 디스크립터 목록을 만든다.

        Returns:
            [
                {
                    "shape_id": "3",                # cNvPr/@id (1차 식별자)
                    "shape_name": "TextBox 2",      # 참고용 (디자이너가 바꿔뒀다면 힌트)
                    "x_emu": 685800,                # 위치 / 크기 (EMU 단위)
                    "y_emu": 457200,
                    "w_emu": 7772400,
                    "h_emu": 914400,
                    "current_text": "여기에 프로젝트명",
                    "is_title_placeholder": True,   # ph type 추론
                    "font_size_pt": 40.0,           # 가장 첫 <a:rPr sz=...>
                    "kind": "text" | "image" | "chart"
                },
                ...
            ]
        """
        doc = parse(slide_xml_path)
        sp_tree = doc.getElementsByTagNameNS(self.PML_NS, "spTree")[0]
        slots: list[dict] = []

        for sp in sp_tree.getElementsByTagNameNS(self.PML_NS, "sp"):
            slot = self._describe_shape(sp)
            if slot is not None:
                slots.append(slot)

        return slots

    # ─────────────────────────────────────────────────────────────
    # 2) LLM 결과 적용
    # ─────────────────────────────────────────────────────────────
    def apply_fills(self, slide_xml_path: str, fills: dict[str, dict]) -> None:
        """
        LLM 응답을 슬라이드 XML 에 적용한다.

        Args:
            fills: {
                "<shape_id>": {
                    "action": "text" | "remove" | "chart",
                    "text": "...",                  # action=text 시
                    "font_size_override": 28,       # 선택 (pt)
                    "is_title": True                # 선택 (b="1" 처리)
                },
                ...
            }
        """
        doc = parse(slide_xml_path)
        sp_tree = doc.getElementsByTagNameNS(self.PML_NS, "spTree")[0]

        for sp in list(sp_tree.getElementsByTagNameNS(self.PML_NS, "sp")):
            shape_id = self._get_shape_id(sp)
            if shape_id is None or shape_id not in fills:
                continue

            fill = fills[shape_id]
            action = fill.get("action", "text")

            if action == "remove":
                sp.parentNode.removeChild(sp)
            elif action == "text":
                self._replace_text(sp, fill)

        with open(slide_xml_path, "w", encoding="utf-8") as f:
            doc.writexml(f, encoding="utf-8")

    # ─────────────────────────────────────────────────────────────
    # 도형 식별
    # ─────────────────────────────────────────────────────────────
    def _get_shape_id(self, sp_element) -> str | None:
        """cNvPr/@id 추출 (네임스페이스 안전)."""
        nvSpPr = sp_element.getElementsByTagNameNS(self.PML_NS, "nvSpPr")
        if not nvSpPr:
            return None
        cNvPr = nvSpPr[0].getElementsByTagNameNS(self.PML_NS, "cNvPr")
        if not cNvPr:
            cNvPr = nvSpPr[0].getElementsByTagNameNS(self.DRAWINGML_NS, "cNvPr")
        if cNvPr:
            value = cNvPr[0].getAttribute("id")
            return value or None
        return None

    def _describe_shape(self, sp_element) -> dict | None:
        """슬롯 디스크립터 생성 (LLM 입력용)."""
        # 구현 세부는 생략 — 핵심 아이디어:
        # 1) cNvPr/@id (필수) / cNvPr/@name (참고)
        # 2) p:spPr/a:xfrm/a:off, a:ext 로 좌표·크기 (EMU)
        # 3) p:nvSpPr/p:nvPr/p:ph 의 type 으로 title 여부 추정
        # 4) p:txBody/a:p/a:r/a:rPr/@sz 로 폰트 크기
        # 5) p:txBody/a:p/a:r/a:t 텍스트 concat → current_text
        # 6) 이미지(blipFill) 가 있으면 kind="image"
        ...
        return None  # 실제 구현 시 채움

    def _replace_text(self, sp_element, fill):
        """
        텍스트 교체 - 원본 서식 완전 보존
        
        - 첫 <a:r>의 <a:rPr>을 기준 서식으로 보존
        - 기존 <a:p> 모두 제거 후 새로 생성
        - 여러 항목은 개별 <a:p>로 분리
        - 공백 보존: xml:space="preserve"
        """
        
        txBody = sp_element.getElementsByTagNameNS(
            self.DRAWINGML_NS, "txBody"
        )[0]
        
        # 기준 서식 추출
        base_pPr = self._extract_paragraph_props(txBody)
        base_rPr = self._extract_run_props(txBody)
        
        # 폰트 크기 오버라이드
        if fill.get("font_size_override"):
            size_val = str(int(fill["font_size_override"] * 100))
            base_rPr.setAttribute("sz", size_val)
        
        # 제목이면 굵게
        if fill.get("is_title"):
            base_rPr.setAttribute("b", "1")
        
        # 기존 <a:p> 모두 제거
        existing_paragraphs = txBody.getElementsByTagNameNS(
            self.DRAWINGML_NS, "p"
        )
        for p in list(existing_paragraphs):
            txBody.removeChild(p)
        
        # 새 텍스트를 줄바꿈 기준으로 <a:p> 분리
        lines = fill["text"].split("\n")
        doc = sp_element.ownerDocument
        
        for line in lines:
            new_p = doc.createElementNS(self.DRAWINGML_NS, "a:p")
            
            if base_pPr:
                new_p.appendChild(base_pPr.cloneNode(deep=True))
            
            new_r = doc.createElementNS(self.DRAWINGML_NS, "a:r")
            new_r.appendChild(base_rPr.cloneNode(deep=True))
            
            new_t = doc.createElementNS(self.DRAWINGML_NS, "a:t")
            if line.startswith(" ") or line.endswith(" ") or "\t" in line:
                new_t.setAttribute("xml:space", "preserve")
            
            new_t.appendChild(doc.createTextNode(line))
            new_r.appendChild(new_t)
            new_p.appendChild(new_r)
            txBody.appendChild(new_p)
    
    def _extract_paragraph_props(self, txBody):
        pPr_list = txBody.getElementsByTagNameNS(self.DRAWINGML_NS, "pPr")
        if pPr_list:
            return pPr_list[0].cloneNode(deep=True)
        return None
    
    def _extract_run_props(self, txBody):
        rPr_list = txBody.getElementsByTagNameNS(self.DRAWINGML_NS, "rPr")
        if rPr_list:
            return rPr_list[0].cloneNode(deep=True)
        return None
```

### 4.4.1 차트 슬롯 처리 (네이티브 캐시 편집 — ADR-0003)

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
| 데이터 초과 | 카테고리가 템플릿 슬롯보다 많으면 LLM 이 요약/절삭해 맞춘다 (§4.3 항목 수 불일치와 동일 철학) |
| 텍스트 전용 필드 | `font_size_override`·`is_title` 는 차트 fill 에 적용되지 않는다 |
