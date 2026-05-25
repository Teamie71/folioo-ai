"""DrawingML 규칙을 준수하는 슬라이드 XML 편집기."""

from pathlib import Path
from typing import Any
from xml.dom import Node
from xml.dom.minidom import Document, Element

from defusedxml.minidom import parse


class SlideEditor:
    """
    디자이너 서식을 보존하면서 텍스트와 차트 데이터만 교체하는 편집기.

    식별자는 PowerPoint 가 자동 부여한 `cNvPr/@id` 만 사용한다.
    """

    PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
    DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
    CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

    _TITLE_PLACEHOLDER_TYPES = {"title", "ctrTitle", "subTitle"}

    def extract_slots(self, slide_xml_path: str) -> list[dict[str, Any]]:
        """
        슬라이드 XML 에서 텍스트 도형과 차트 Slot 디스크립터를 추출한다.

        Returns:
            shape_id, 좌표/크기(EMU), 현재 텍스트, 폰트 크기, kind 를 담은 dict 목록
        """
        doc = parse(slide_xml_path)
        sp_tree = self._first_descendant(doc, self.PML_NS, "spTree")
        if sp_tree is None:
            return []

        slots: list[dict[str, Any]] = []
        for sp_element in self._descendants(sp_tree, self.PML_NS, "sp"):
            slot = self._describe_shape(sp_element)
            if slot is not None:
                slots.append(slot)

        for graphic_frame in self._descendants(sp_tree, self.PML_NS, "graphicFrame"):
            slot = self._describe_graphic_frame(graphic_frame, slide_xml_path)
            if slot is not None:
                slots.append(slot)

        return slots

    def apply_fills(self, slide_xml_path: str, fills: dict[str, dict[str, Any]]) -> None:
        """
        평평한 shape_id -> fill 맵을 슬라이드 XML 에 적용한다.

        `text` 와 `remove` 는 슬라이드 XML 을 수정하고, `chart` 는 rels 로 연결된
        chartN.xml 의 네이티브 캐시를 수정한다.
        """
        doc = parse(slide_xml_path)
        sp_tree = self._first_descendant(doc, self.PML_NS, "spTree")
        if sp_tree is None:
            return

        for sp_element in list(self._descendants(sp_tree, self.PML_NS, "sp")):
            shape_id = self._get_shape_id(sp_element)
            if shape_id is None or shape_id not in fills:
                continue

            fill = fills[shape_id]
            action = fill.get("action", "text")
            if action == "remove":
                sp_element.parentNode.removeChild(sp_element)
            elif action == "text":
                self._replace_text(sp_element, fill)

        for graphic_frame in list(self._descendants(sp_tree, self.PML_NS, "graphicFrame")):
            shape_id = self._get_shape_id(graphic_frame)
            if shape_id is None or shape_id not in fills:
                continue

            fill = fills[shape_id]
            action = fill.get("action")
            if action == "remove":
                graphic_frame.parentNode.removeChild(graphic_frame)
            elif action == "chart":
                self._replace_chart_cache(slide_xml_path, graphic_frame, fill)

        self._write_document(Path(slide_xml_path), doc)

    def _get_shape_id(self, element: Element) -> str | None:
        """cNvPr/@id 값을 추출한다."""
        cnv_pr = self._get_cnv_pr(element)
        if cnv_pr is None:
            return None
        shape_id = cnv_pr.getAttribute("id")
        return shape_id or None

    def _describe_shape(self, sp_element: Element) -> dict[str, Any] | None:
        """텍스트 또는 이미지 도형 Slot 디스크립터를 만든다."""
        shape_id = self._get_shape_id(sp_element)
        if shape_id is None:
            return None

        cnv_pr = self._get_cnv_pr(sp_element)
        tx_body = self._first_descendant(sp_element, self.PML_NS, "txBody")
        kind = "image" if self._has_image_fill(sp_element) else "text"

        return {
            "shape_id": shape_id,
            "shape_name": cnv_pr.getAttribute("name") if cnv_pr is not None else "",
            **self._coordinates(sp_element),
            "current_text": self._text_body_content(tx_body) if tx_body is not None else "",
            "is_title_placeholder": self._is_title_placeholder(sp_element),
            "font_size_pt": self._first_font_size_pt(tx_body) if tx_body is not None else None,
            "kind": kind,
        }

    def _describe_graphic_frame(
        self,
        graphic_frame: Element,
        slide_xml_path: str,
    ) -> dict[str, Any] | None:
        """차트 graphicFrame Slot 디스크립터를 만든다."""
        shape_id = self._get_shape_id(graphic_frame)
        if shape_id is None:
            return None

        chart = self._first_descendant(graphic_frame, self.CHART_NS, "chart")
        if chart is None:
            return None

        chart_path = self._chart_path_for_graphic_frame(slide_xml_path, graphic_frame)
        chart_summary = self._chart_summary(chart_path) if chart_path is not None else {}
        cnv_pr = self._get_cnv_pr(graphic_frame)

        return {
            "shape_id": shape_id,
            "shape_name": cnv_pr.getAttribute("name") if cnv_pr is not None else "",
            **self._coordinates(graphic_frame),
            "current_text": chart_summary.get("title", ""),
            "is_title_placeholder": False,
            "font_size_pt": None,
            "kind": "chart",
            "chart_rel_id": self._chart_rel_id(chart),
            "chart_type": chart_summary.get("chart_type"),
            "categories": chart_summary.get("categories", []),
            "series": chart_summary.get("series", []),
        }

    def _replace_text(self, sp_element: Element, fill: dict[str, Any]) -> None:
        """도형 내부 텍스트를 교체하되 첫 pPr/rPr 서식을 보존한다."""
        doc = sp_element.ownerDocument
        tx_body = self._first_descendant(sp_element, self.PML_NS, "txBody")
        if tx_body is None:
            tx_body = self._create_text_body(doc)
            sp_element.appendChild(tx_body)

        base_p_pr = self._extract_paragraph_props(tx_body)
        base_r_pr = self._extract_run_props(tx_body, doc)

        if fill.get("font_size_override") is not None:
            size_val = str(int(round(float(fill["font_size_override"]) * 100)))
            base_r_pr.setAttribute("sz", size_val)

        if fill.get("is_title"):
            base_r_pr.setAttribute("b", "1")

        for paragraph in list(self._children(tx_body, self.DRAWINGML_NS, "p")):
            tx_body.removeChild(paragraph)

        text = str(fill.get("text", ""))
        for line in text.split("\n"):
            new_p = doc.createElementNS(self.DRAWINGML_NS, "a:p")
            if base_p_pr is not None:
                new_p.appendChild(base_p_pr.cloneNode(deep=True))

            new_r = doc.createElementNS(self.DRAWINGML_NS, "a:r")
            new_r.appendChild(base_r_pr.cloneNode(deep=True))

            new_t = doc.createElementNS(self.DRAWINGML_NS, "a:t")
            new_t.setAttribute("xml:space", "preserve")
            new_t.appendChild(doc.createTextNode(line))

            new_r.appendChild(new_t)
            new_p.appendChild(new_r)
            tx_body.appendChild(new_p)

    def _replace_chart_cache(
        self,
        slide_xml_path: str,
        graphic_frame: Element,
        fill: dict[str, Any],
    ) -> None:
        """chartN.xml 의 strCache/numCache/ptCount/c:f 를 일관되게 갱신한다."""
        chart_path = self._chart_path_for_graphic_frame(slide_xml_path, graphic_frame)
        if chart_path is None:
            raise ValueError("차트 관계 정보를 찾을 수 없습니다.")

        data = fill.get("data") or {}
        series_data = data.get("series") or []
        if not series_data:
            raise ValueError("차트 fill 에는 data.series 가 필요합니다.")

        first_values = series_data[0].get("values") or []
        categories = [str(value) for value in data.get("categories") or []]
        if not categories:
            categories = [str(index + 1) for index in range(len(first_values))]

        for series in series_data:
            values = series.get("values") or []
            if len(values) != len(categories):
                raise ValueError("차트 categories 와 values 길이가 일치해야 합니다.")

        chart_doc = parse(str(chart_path))
        chart_element = self._first_chart_type_element(chart_doc)
        if chart_element is None:
            raise ValueError("지원되는 차트 타입을 찾을 수 없습니다.")

        existing_series = self._children(chart_element, self.CHART_NS, "ser")
        if not existing_series:
            raise ValueError("차트에 복제할 series 템플릿이 없습니다.")

        template_series = existing_series[0].cloneNode(deep=True)
        self._resize_series(chart_element, existing_series, template_series, len(series_data))

        sheet_name = self._first_formula_sheet(chart_doc) or "Sheet1"
        category_formula = self._range_formula(sheet_name, "A", 2, len(categories) + 1)

        for index, series in enumerate(self._children(chart_element, self.CHART_NS, "ser")):
            values = series_data[index].get("values") or []
            value_column = self._excel_column(index + 2)
            self._replace_series_cache(
                series,
                index=index,
                name=str(series_data[index].get("name", f"Series {index + 1}")),
                categories=categories,
                values=values,
                category_formula=category_formula,
                name_formula=self._cell_formula(sheet_name, value_column, 1),
                value_formula=self._range_formula(sheet_name, value_column, 2, len(values) + 1),
            )

        self._write_document(chart_path, chart_doc)

    def _resize_series(
        self,
        chart_element: Element,
        existing_series: list[Element],
        template_series: Node,
        target_count: int,
    ) -> None:
        """차트 타입은 유지하면서 c:ser 개수만 데이터에 맞춘다."""
        while len(existing_series) > target_count:
            series = existing_series.pop()
            chart_element.removeChild(series)

        while len(existing_series) < target_count:
            new_series = template_series.cloneNode(deep=True)
            last_series = existing_series[-1]
            if last_series.nextSibling is not None:
                chart_element.insertBefore(new_series, last_series.nextSibling)
            else:
                chart_element.appendChild(new_series)
            existing_series.append(new_series)

    def _replace_series_cache(
        self,
        series: Element,
        *,
        index: int,
        name: str,
        categories: list[str],
        values: list[Any],
        category_formula: str,
        name_formula: str,
        value_formula: str,
    ) -> None:
        self._set_val_attribute(self._first_child(series, self.CHART_NS, "idx"), index)
        self._set_val_attribute(self._first_child(series, self.CHART_NS, "order"), index)

        tx_ref = self._ensure_ref(series, "tx", "strRef")
        self._set_formula(tx_ref, name_formula)
        self._replace_cache_points(self._ensure_cache(tx_ref, "strCache"), [name])

        cat_ref = self._ensure_ref(series, "cat", "strRef")
        self._set_formula(cat_ref, category_formula)
        self._replace_cache_points(self._ensure_cache(cat_ref, "strCache"), categories)

        val_ref = self._ensure_ref(series, "val", "numRef")
        self._set_formula(val_ref, value_formula)
        self._replace_cache_points(self._ensure_cache(val_ref, "numCache"), values)

    def _get_cnv_pr(self, element: Element) -> Element | None:
        for tag_name in ("nvSpPr", "nvGraphicFramePr", "nvPicPr"):
            nv_props = self._first_descendant(element, self.PML_NS, tag_name)
            if nv_props is None:
                continue

            cnv_pr = self._first_descendant(nv_props, self.PML_NS, "cNvPr")
            if cnv_pr is None:
                cnv_pr = self._first_descendant(nv_props, self.DRAWINGML_NS, "cNvPr")
            return cnv_pr

        return None

    def _coordinates(self, element: Element) -> dict[str, int | None]:
        xfrm = self._first_descendant(element, self.PML_NS, "xfrm")
        if xfrm is None:
            xfrm = self._first_descendant(element, self.DRAWINGML_NS, "xfrm")

        off = self._first_descendant(xfrm, self.DRAWINGML_NS, "off") if xfrm else None
        ext = self._first_descendant(xfrm, self.DRAWINGML_NS, "ext") if xfrm else None

        return {
            "x_emu": self._int_attr(off, "x"),
            "y_emu": self._int_attr(off, "y"),
            "w_emu": self._int_attr(ext, "cx"),
            "h_emu": self._int_attr(ext, "cy"),
        }

    def _is_title_placeholder(self, sp_element: Element) -> bool:
        ph = self._first_descendant(sp_element, self.PML_NS, "ph")
        return ph is not None and ph.getAttribute("type") in self._TITLE_PLACEHOLDER_TYPES

    def _has_image_fill(self, sp_element: Element) -> bool:
        return (
            self._first_descendant(sp_element, self.PML_NS, "blipFill") is not None
            or self._first_descendant(sp_element, self.DRAWINGML_NS, "blip") is not None
        )

    def _text_body_content(self, tx_body: Element) -> str:
        lines = []
        for paragraph in self._children(tx_body, self.DRAWINGML_NS, "p"):
            parts = [
                self._node_text(text_node)
                for text_node in self._descendants(paragraph, self.DRAWINGML_NS, "t")
            ]
            lines.append("".join(parts))
        return "\n".join(lines)

    def _first_font_size_pt(self, tx_body: Element) -> float | None:
        for tag_name in ("rPr", "defRPr", "endParaRPr"):
            for run_props in self._descendants(tx_body, self.DRAWINGML_NS, tag_name):
                size = run_props.getAttribute("sz")
                if not size:
                    continue
                try:
                    return int(size) / 100
                except ValueError:
                    return None
        return None

    def _extract_paragraph_props(self, tx_body: Element) -> Element | None:
        paragraph_props = self._first_descendant(tx_body, self.DRAWINGML_NS, "pPr")
        return paragraph_props.cloneNode(deep=True) if paragraph_props is not None else None

    def _extract_run_props(self, tx_body: Element, doc: Document) -> Element:
        run_props = self._first_descendant(tx_body, self.DRAWINGML_NS, "rPr")
        if run_props is not None:
            return run_props.cloneNode(deep=True)
        return doc.createElementNS(self.DRAWINGML_NS, "a:rPr")

    def _create_text_body(self, doc: Document) -> Element:
        tx_body = doc.createElementNS(self.PML_NS, "p:txBody")
        tx_body.appendChild(doc.createElementNS(self.DRAWINGML_NS, "a:bodyPr"))
        tx_body.appendChild(doc.createElementNS(self.DRAWINGML_NS, "a:lstStyle"))
        return tx_body

    def _chart_path_for_graphic_frame(
        self,
        slide_xml_path: str,
        graphic_frame: Element,
    ) -> Path | None:
        chart = self._first_descendant(graphic_frame, self.CHART_NS, "chart")
        if chart is None:
            return None

        rel_id = self._chart_rel_id(chart)
        if rel_id is None:
            return None

        target = self._relationship_target(Path(slide_xml_path), rel_id)
        if target is None:
            return None

        return self._resolve_part_path(Path(slide_xml_path), target)

    def _chart_rel_id(self, chart: Element) -> str | None:
        rel_id = chart.getAttributeNS(self.REL_NS, "id") or chart.getAttribute("r:id")
        return rel_id or None

    def _relationship_target(self, slide_xml_path: Path, rel_id: str) -> str | None:
        rels_path = slide_xml_path.parent / "_rels" / f"{slide_xml_path.name}.rels"
        if not rels_path.exists():
            return None

        rels_doc = parse(str(rels_path))
        relationships = self._descendants(rels_doc, self.PKG_REL_NS, "Relationship")
        if not relationships:
            relationships = list(rels_doc.getElementsByTagName("Relationship"))

        for relationship in relationships:
            if relationship.getAttribute("Id") == rel_id:
                return relationship.getAttribute("Target") or None

        return None

    def _resolve_part_path(self, source_xml_path: Path, target: str) -> Path:
        normalized_target = target.replace("\\", "/")
        if not normalized_target.startswith("/"):
            return (source_xml_path.parent / normalized_target).resolve()

        package_target = normalized_target.lstrip("/")
        for parent in (source_xml_path.parent, *source_xml_path.parents):
            candidate = parent / package_target
            if candidate.exists():
                return candidate.resolve()

        return (source_xml_path.parent / package_target).resolve()

    def _chart_summary(self, chart_path: Path) -> dict[str, Any]:
        chart_doc = parse(str(chart_path))
        chart_element = self._first_chart_type_element(chart_doc)
        if chart_element is None:
            return {}

        return {
            "title": self._chart_title(chart_doc),
            "chart_type": self._chart_type(chart_element),
            "categories": self._chart_categories(chart_element),
            "series": self._chart_series(chart_element),
        }

    def _first_chart_type_element(self, chart_doc: Document) -> Element | None:
        plot_area = self._first_descendant(chart_doc, self.CHART_NS, "plotArea")
        if plot_area is None:
            return None

        for child in self._element_children(plot_area):
            if child.namespaceURI == self.CHART_NS and child.localName.endswith("Chart"):
                return child

        return None

    def _chart_type(self, chart_element: Element) -> str:
        local_name = chart_element.localName or ""
        return local_name.removesuffix("Chart")

    def _chart_title(self, chart_doc: Document) -> str:
        title = self._first_descendant(chart_doc, self.CHART_NS, "title")
        if title is None:
            return ""
        return "".join(
            self._node_text(text_node)
            for text_node in self._descendants(title, self.DRAWINGML_NS, "t")
        )

    def _chart_categories(self, chart_element: Element) -> list[str]:
        first_series = self._first_child(chart_element, self.CHART_NS, "ser")
        if first_series is None:
            return []
        cat = self._first_child(first_series, self.CHART_NS, "cat")
        if cat is None:
            return []
        return self._cached_values(cat, "strCache")

    def _chart_series(self, chart_element: Element) -> list[dict[str, Any]]:
        series_list = []
        for series in self._children(chart_element, self.CHART_NS, "ser"):
            tx = self._first_child(series, self.CHART_NS, "tx")
            val = self._first_child(series, self.CHART_NS, "val")
            series_list.append(
                {
                    "name": self._series_name(tx),
                    "values": [
                        self._parse_number(value) for value in self._cached_values(val, "numCache")
                    ]
                    if val is not None
                    else [],
                }
            )
        return series_list

    def _series_name(self, tx: Element | None) -> str:
        if tx is None:
            return ""

        cached_names = self._cached_values(tx, "strCache")
        if cached_names:
            return cached_names[0]

        value = self._first_child(tx, self.CHART_NS, "v")
        return self._node_text(value) if value is not None else ""

    def _cached_values(self, parent: Element | None, cache_tag: str) -> list[str]:
        if parent is None:
            return []

        cache = self._first_descendant(parent, self.CHART_NS, cache_tag)
        if cache is None:
            return []

        values = []
        for point in self._children(cache, self.CHART_NS, "pt"):
            value = self._first_child(point, self.CHART_NS, "v")
            values.append(self._node_text(value) if value is not None else "")
        return values

    def _ensure_ref(self, series: Element, parent_tag: str, ref_tag: str) -> Element:
        parent = self._first_child(series, self.CHART_NS, parent_tag)
        if parent is None:
            parent = series.ownerDocument.createElementNS(self.CHART_NS, f"c:{parent_tag}")
            series.appendChild(parent)

        ref = self._first_child(parent, self.CHART_NS, ref_tag)
        if ref is None:
            ref = series.ownerDocument.createElementNS(self.CHART_NS, f"c:{ref_tag}")
            parent.appendChild(ref)

        return ref

    def _ensure_cache(self, ref: Element, cache_tag: str) -> Element:
        cache = self._first_child(ref, self.CHART_NS, cache_tag)
        if cache is None:
            cache = ref.ownerDocument.createElementNS(self.CHART_NS, f"c:{cache_tag}")
            ref.appendChild(cache)
        return cache

    def _set_formula(self, ref: Element, formula: str) -> None:
        formula_element = self._first_child(ref, self.CHART_NS, "f")
        if formula_element is None:
            formula_element = ref.ownerDocument.createElementNS(self.CHART_NS, "c:f")
            first_cache = self._first_cache_child(ref)
            if first_cache is not None:
                ref.insertBefore(formula_element, first_cache)
            else:
                ref.appendChild(formula_element)
        self._replace_text_node(formula_element, formula)

    def _replace_cache_points(self, cache: Element, values: list[Any]) -> None:
        point_count = self._first_child(cache, self.CHART_NS, "ptCount")
        if point_count is None:
            point_count = cache.ownerDocument.createElementNS(self.CHART_NS, "c:ptCount")
            first_point = self._first_child(cache, self.CHART_NS, "pt")
            if first_point is not None:
                cache.insertBefore(point_count, first_point)
            else:
                cache.appendChild(point_count)

        point_count.setAttribute("val", str(len(values)))

        for point in list(self._children(cache, self.CHART_NS, "pt")):
            cache.removeChild(point)

        for index, value in enumerate(values):
            point = cache.ownerDocument.createElementNS(self.CHART_NS, "c:pt")
            point.setAttribute("idx", str(index))
            value_element = cache.ownerDocument.createElementNS(self.CHART_NS, "c:v")
            value_element.appendChild(
                cache.ownerDocument.createTextNode(self._format_cache_value(value))
            )
            point.appendChild(value_element)
            cache.appendChild(point)

    def _first_cache_child(self, ref: Element) -> Element | None:
        for child in self._element_children(ref):
            if child.namespaceURI == self.CHART_NS and child.localName in {"strCache", "numCache"}:
                return child
        return None

    def _set_val_attribute(self, element: Element | None, value: int) -> None:
        if element is not None:
            element.setAttribute("val", str(value))

    def _first_formula_sheet(self, chart_doc: Document) -> str | None:
        for formula in self._descendants(chart_doc, self.CHART_NS, "f"):
            formula_text = self._node_text(formula)
            if "!" in formula_text:
                return formula_text.split("!", 1)[0]
        return None

    def _cell_formula(self, sheet_name: str, column: str, row: int) -> str:
        return f"{sheet_name}!${column}${row}"

    def _range_formula(self, sheet_name: str, column: str, start_row: int, end_row: int) -> str:
        return f"{sheet_name}!${column}${start_row}:${column}${end_row}"

    def _excel_column(self, one_based_index: int) -> str:
        column = ""
        index = one_based_index
        while index:
            index, remainder = divmod(index - 1, 26)
            column = chr(ord("A") + remainder) + column
        return column

    def _children(self, element: Element, namespace_uri: str, local_name: str) -> list[Element]:
        return [
            child
            for child in self._element_children(element)
            if child.namespaceURI == namespace_uri and child.localName == local_name
        ]

    def _first_child(
        self,
        element: Element | Document | None,
        namespace_uri: str,
        local_name: str,
    ) -> Element | None:
        if element is None:
            return None
        for child in self._element_children(element):
            if child.namespaceURI == namespace_uri and child.localName == local_name:
                return child
        return None

    def _descendants(
        self,
        element: Element | Document,
        namespace_uri: str,
        local_name: str,
    ) -> list[Element]:
        return list(element.getElementsByTagNameNS(namespace_uri, local_name))

    def _first_descendant(
        self,
        element: Element | Document | None,
        namespace_uri: str,
        local_name: str,
    ) -> Element | None:
        if element is None:
            return None
        descendants = element.getElementsByTagNameNS(namespace_uri, local_name)
        return descendants[0] if descendants else None

    def _element_children(self, element: Element | Document) -> list[Element]:
        return [child for child in element.childNodes if child.nodeType == Node.ELEMENT_NODE]

    def _int_attr(self, element: Element | None, attr_name: str) -> int | None:
        if element is None or not element.hasAttribute(attr_name):
            return None
        return int(element.getAttribute(attr_name))

    def _node_text(self, element: Element | None) -> str:
        if element is None:
            return ""
        return "".join(
            child.data
            for child in element.childNodes
            if child.nodeType in {Node.TEXT_NODE, Node.CDATA_SECTION_NODE}
        )

    def _replace_text_node(self, element: Element, value: str) -> None:
        for child in list(element.childNodes):
            element.removeChild(child)
        element.appendChild(element.ownerDocument.createTextNode(value))

    def _format_cache_value(self, value: Any) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _parse_number(self, value: str) -> int | float | str:
        try:
            number = float(value)
        except ValueError:
            return value
        if number.is_integer():
            return int(number)
        return number

    def _write_document(self, path: Path, doc: Document) -> None:
        with path.open("w", encoding="utf-8") as file:
            doc.writexml(file, encoding="utf-8")
