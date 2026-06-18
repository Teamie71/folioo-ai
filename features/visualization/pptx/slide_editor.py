"""슬라이드 XML Slot 추출과 Fill 적용을 담당하는 OOXML 편집기."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.dom import Node
from xml.dom.minidom import Document, Element

from defusedxml.minidom import parse


class SlideEditor:
    """
    디자이너 서식을 보존하면서 텍스트와 차트 데이터만 교체하는 편집기.

    식별자는 PowerPoint 가 자동 부여한 `cNvPr/@id` 만 사용한다.

    Attributes:
        PML_NS: PresentationML 네임스페이스 URI
        DRAWINGML_NS: DrawingML 네임스페이스 URI
        CHART_NS: DrawingML Chart 네임스페이스 URI
        REL_NS: Office relationship 네임스페이스 URI
        PKG_REL_NS: OOXML package relationship 네임스페이스 URI
    """

    PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
    DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
    CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

    _TITLE_PLACEHOLDER_TYPES = frozenset({"title", "ctrTitle", "subTitle"})
    _TEXT_ALLOWED_ACTIONS = ("text", "remove")
    _CHART_ALLOWED_ACTIONS = ("chart",)
    _GEOMETRY_SHAPE_TAGS = ("sp", "pic", "graphicFrame")
    _EXACT_MARKER_RGB = "FF0000"
    _OUTPUT_TEXT_COLOR_FALLBACK_RGB = "000000"
    _HEX_DIGITS = frozenset("0123456789ABCDEFabcdef")

    def extract_slots(self, slide_xml_path: str) -> list[dict[str, Any]]:
        """
        슬라이드 XML 에서 텍스트 도형과 차트 Slot 디스크립터를 추출한다.

        Args:
            slide_xml_path: 편집 대상 `slideN.xml` 파일 경로

        Returns:
            list[dict[str, Any]]: Slot 디스크립터 목록. 각 항목은 `shape_id`,
            `shape_name`, `x_emu`, `y_emu`, `w_emu`, `h_emu`, `current_text`,
            `is_title_placeholder`, `font_size_pt`, `kind`, `editable`, `required`,
            `allowed_actions` 를 포함한다. 텍스트가 없는 장식 도형은 LLM fill
            대상에서 제외한다.
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

    def apply_fills(
        self,
        slide_xml_path: str,
        fills: dict[str, dict[str, Any]],
        slot_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[str]:
        """
        평평한 shape_id -> fill 맵을 슬라이드 XML 에 적용한다.

        Args:
            slide_xml_path: 편집 대상 `slideN.xml` 파일 경로
            fills: `shape_id` 를 key 로 하는 Fill 맵. 각 값은 `action`,
                `text`, `font_size_override`, `is_title`, `data` 를 포함할 수 있다.
            slot_metadata: `shape_id` 를 key 로 하는 v2 slot metadata. `marker_color`,
                `output_text_color` 는 fill payload 보다 낮은 우선순위로 병합한다.

        Returns:
            list[str]: marker color fallback 등 적용 중 발생한 warning 목록

        Raises:
            ValueError: 차트 관계, 차트 타입, series 데이터가 유효하지 않은 경우
        """
        doc = parse(slide_xml_path)
        sp_tree = self._first_descendant(doc, self.PML_NS, "spTree")
        if sp_tree is None:
            return []

        warnings: list[str] = []
        for sp_element in list(self._descendants(sp_tree, self.PML_NS, "sp")):
            shape_id = self._get_shape_id(sp_element)
            if shape_id is None or shape_id not in fills:
                continue

            fill = self._merge_slot_style_metadata(shape_id, fills[shape_id], slot_metadata)
            action = fill.get("action", "text")
            if action == "remove":
                sp_element.parentNode.removeChild(sp_element)
            elif action == "text":
                warnings.extend(self._replace_text(sp_element, fill))

        for graphic_frame in list(self._descendants(sp_tree, self.PML_NS, "graphicFrame")):
            shape_id = self._get_shape_id(graphic_frame)
            if shape_id is None or shape_id not in fills:
                continue

            fill = self._merge_slot_style_metadata(shape_id, fills[shape_id], slot_metadata)
            action = fill.get("action")
            if action == "remove":
                graphic_frame.parentNode.removeChild(graphic_frame)
            elif action == "chart":
                self._replace_chart_cache(slide_xml_path, graphic_frame, fill)

        self._write_document(Path(slide_xml_path), doc)
        return warnings

    def _merge_slot_style_metadata(
        self,
        shape_id: str,
        fill: dict[str, Any],
        slot_metadata: Mapping[str, Mapping[str, Any]] | None,
    ) -> dict[str, Any]:
        if slot_metadata is None:
            return fill

        metadata = slot_metadata.get(shape_id)
        if not isinstance(metadata, Mapping):
            return fill

        merged_fill: dict[str, Any] | None = None
        for field_name in ("marker_color", "output_text_color", "text_replacement_mode"):
            if field_name in fill or not metadata.get(field_name):
                continue
            if merged_fill is None:
                merged_fill = dict(fill)
            merged_fill[field_name] = metadata[field_name]
        return merged_fill or fill

    def apply_layout_actions(
        self,
        slide_xml_path: str,
        layout_actions: Sequence[Mapping[str, Any]],
    ) -> None:
        """
        워커 내부 layout action 을 슬라이드 OOXML geometry 에 적용한다.

        Args:
            slide_xml_path: 편집 대상 `slideN.xml` 파일 경로
            layout_actions: `resize_shape`, `resize_linked_shape` action 목록

        Raises:
            ValueError: action 종류, shape id, geometry payload 가 유효하지 않은 경우
        """
        if not layout_actions:
            return

        doc = parse(slide_xml_path)
        sp_tree = self._first_descendant(doc, self.PML_NS, "spTree")
        if sp_tree is None:
            return

        shapes_by_id = self._geometry_shapes_by_id(sp_tree)
        for action in layout_actions:
            if not isinstance(action, Mapping):
                raise ValueError("layout action 은 mapping 이어야 합니다.")

            action_type = action.get("action")
            if action_type == "resize_shape":
                self._apply_resize_shape_action(action, shapes_by_id)
            elif action_type == "resize_linked_shape":
                self._apply_resize_linked_shape_action(action, shapes_by_id)
            elif action_type == "relayout_row":
                self._apply_relayout_row_action(action, shapes_by_id)
            else:
                raise ValueError(f"지원하지 않는 layout action 입니다: {action_type}")

        self._write_document(Path(slide_xml_path), doc)

    def clear_content(self, slide_xml_path: str) -> None:
        """
        슬라이드의 가시 콘텐츠 도형을 모두 제거해 빈 페이지로 만든다.

        마스터/배경은 보존하고 `spTree` 의 기본 그룹 속성(`nvGrpSpPr`, `grpSpPr`)은
        남긴다. 콘텐츠 생성 실패 슬라이드가 템플릿 예시 문구를 노출하지 않도록
        만드는 용도다.
        """
        doc = parse(slide_xml_path)
        sp_tree = self._first_descendant(doc, self.PML_NS, "spTree")
        if sp_tree is None:
            return

        removable = {"sp", "pic", "graphicFrame", "cxnSp", "grpSp"}
        for child in list(sp_tree.childNodes):
            if child.nodeType != Node.ELEMENT_NODE:
                continue
            if child.namespaceURI == self.PML_NS and child.localName in removable:
                sp_tree.removeChild(child)
                child.unlink()

        self._write_document(Path(slide_xml_path), doc)

    def _apply_resize_shape_action(
        self,
        action: Mapping[str, Any],
        shapes_by_id: dict[str, Element],
    ) -> None:
        """단일 shape geometry 를 action payload 대로 변경한다."""
        shape = self._required_geometry_shape(action.get("shape_id"), shapes_by_id)
        updates = self._geometry_updates(action)
        if not updates:
            raise ValueError("resize_shape action 에는 geometry 값이 필요합니다.")
        self._apply_geometry_updates(shape, updates)

    def _apply_resize_linked_shape_action(
        self,
        action: Mapping[str, Any],
        shapes_by_id: dict[str, Element],
    ) -> None:
        """텍스트 shape 와 연결된 배경 shape geometry 를 함께 변경한다."""
        text_shape = self._required_geometry_shape(action.get("shape_id"), shapes_by_id)
        text_updates = self._geometry_updates(action)
        if not text_updates:
            raise ValueError("resize_linked_shape action 에는 text geometry 값이 필요합니다.")

        linked_shape_ids = self._linked_shape_ids(action)
        linked_updates = self._linked_geometry_updates(action)
        if not linked_updates:
            raise ValueError("resize_linked_shape action 에는 linked geometry 값이 필요합니다.")

        self._apply_geometry_updates(text_shape, text_updates)
        for linked_shape_id in linked_shape_ids:
            linked_shape = self._required_geometry_shape(linked_shape_id, shapes_by_id)
            self._apply_geometry_updates(linked_shape, linked_updates)

    def _apply_relayout_row_action(
        self,
        action: Mapping[str, Any],
        shapes_by_id: dict[str, Element],
    ) -> None:
        """inline label row item 과 연결 배경의 x 좌표를 함께 재배치한다."""
        min_gap_emu = self._non_negative_geometry_int(action.get("min_gap_emu", 0), "min_gap_emu")
        row_items = self._relayout_row_items(action)
        resolved_items = [
            self._resolve_relayout_row_item(item, index, shapes_by_id)
            for index, item in enumerate(row_items)
        ]

        self._validate_relayout_row_gap(resolved_items, min_gap_emu)
        for item in resolved_items:
            self._apply_geometry_updates(item["shape"], item["updates"])
            for linked_item in item["linked_items"]:
                self._apply_geometry_updates(linked_item["shape"], linked_item["updates"])

    def _relayout_row_items(self, action: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        items = action.get("items")
        if not isinstance(items, Sequence) or isinstance(items, str | bytes):
            raise ValueError("relayout_row action 에는 items 목록이 필요합니다.")

        row_items: list[Mapping[str, Any]] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError("relayout_row item 은 mapping 이어야 합니다.")
            row_items.append(item)

        if not row_items:
            raise ValueError("relayout_row action 에는 items 목록이 필요합니다.")
        return row_items

    def _resolve_relayout_row_item(
        self,
        item: Mapping[str, Any],
        index: int,
        shapes_by_id: dict[str, Element],
    ) -> dict[str, Any]:
        shape = self._required_geometry_shape(item.get("shape_id"), shapes_by_id)
        x_emu = self._geometry_int(item.get("x_emu"), f"items[{index}].x_emu", positive=False)
        w_emu = self._relayout_width_emu(shape, item, index)
        updates: dict[str, int] = {"x_emu": x_emu}
        if "w_emu" in item:
            updates["w_emu"] = w_emu

        linked_items = []
        visible_left = x_emu
        visible_right = x_emu + w_emu
        if item.get("linked_shape_ids") is not None or item.get("linked_shape_id") is not None:
            linked_shape_ids = self._linked_shape_ids(item)
            current_x = self._required_current_geometry(shape, "x_emu")
            for linked_shape_id in linked_shape_ids:
                linked_shape = self._required_geometry_shape(linked_shape_id, shapes_by_id)
                linked_updates = self._relayout_linked_updates(
                    item,
                    index,
                    x_emu=x_emu,
                    current_x=current_x,
                    linked_shape=linked_shape,
                )
                linked_items.append({"shape": linked_shape, "updates": linked_updates})
                linked_x = linked_updates["x_emu"]
                linked_w = linked_updates.get(
                    "w_emu",
                    self._required_current_geometry(linked_shape, "w_emu"),
                )
                visible_left = min(visible_left, linked_x)
                visible_right = max(visible_right, linked_x + linked_w)

        return {
            "shape": shape,
            "shape_id": self._get_shape_id(shape) or "",
            "x_emu": x_emu,
            "w_emu": w_emu,
            "visible_left_emu": visible_left,
            "visible_right_emu": visible_right,
            "updates": updates,
            "linked_items": linked_items,
        }

    def _relayout_width_emu(
        self,
        shape: Element,
        item: Mapping[str, Any],
        index: int,
    ) -> int:
        if "w_emu" in item:
            return self._geometry_int(item["w_emu"], f"items[{index}].w_emu", positive=True)
        return self._required_current_geometry(shape, "w_emu")

    def _relayout_linked_updates(
        self,
        item: Mapping[str, Any],
        index: int,
        *,
        x_emu: int,
        current_x: int,
        linked_shape: Element,
    ) -> dict[str, int]:
        updates: dict[str, int] = {}
        if "linked_x_emu" in item:
            updates["x_emu"] = self._geometry_int(
                item["linked_x_emu"],
                f"items[{index}].linked_x_emu",
                positive=False,
            )
        else:
            current_linked_x = self._required_current_geometry(linked_shape, "x_emu")
            updates["x_emu"] = x_emu + (current_linked_x - current_x)

        if "linked_w_emu" in item:
            updates["w_emu"] = self._geometry_int(
                item["linked_w_emu"],
                f"items[{index}].linked_w_emu",
                positive=True,
            )
        return updates

    def _validate_relayout_row_gap(
        self,
        resolved_items: Sequence[Mapping[str, Any]],
        min_gap_emu: int,
    ) -> None:
        previous_right: int | None = None
        previous_shape_id = ""
        for item in resolved_items:
            visible_left = int(item["visible_left_emu"])
            visible_right = int(item["visible_right_emu"])
            shape_id = str(item["shape_id"])
            if previous_right is not None and visible_left - previous_right < min_gap_emu:
                raise ValueError(
                    "relayout_row item 순서 또는 min_gap_emu 를 만족하지 않습니다: "
                    f"{previous_shape_id} -> {shape_id}"
                )
            previous_right = visible_right
            previous_shape_id = shape_id

    def _geometry_shapes_by_id(self, sp_tree: Element) -> dict[str, Element]:
        """geometry action 대상이 될 수 있는 shape 를 cNvPr/@id 로 색인한다."""
        shapes_by_id: dict[str, Element] = {}
        for tag_name in self._GEOMETRY_SHAPE_TAGS:
            for element in self._descendants(sp_tree, self.PML_NS, tag_name):
                shape_id = self._get_shape_id(element)
                if shape_id is not None and shape_id not in shapes_by_id:
                    shapes_by_id[shape_id] = element
        return shapes_by_id

    def _required_geometry_shape(
        self,
        shape_id_value: Any,
        shapes_by_id: dict[str, Element],
    ) -> Element:
        shape_id = self._required_shape_id(shape_id_value)
        shape = shapes_by_id.get(shape_id)
        if shape is None:
            raise ValueError(f"layout action 대상 shape_id 를 찾을 수 없습니다: {shape_id}")
        return shape

    def _required_shape_id(self, shape_id_value: Any) -> str:
        if shape_id_value is None:
            raise ValueError("layout action 에는 shape_id 가 필요합니다.")

        shape_id = str(shape_id_value).strip()
        if not shape_id:
            raise ValueError("layout action shape_id 는 비어 있을 수 없습니다.")
        return shape_id

    def _linked_shape_ids(self, action: Mapping[str, Any]) -> list[str]:
        linked_shape_ids = action.get("linked_shape_ids")
        if linked_shape_ids is None and action.get("linked_shape_id") is not None:
            linked_shape_ids = [action.get("linked_shape_id")]

        if not isinstance(linked_shape_ids, Sequence) or isinstance(linked_shape_ids, str | bytes):
            raise ValueError("resize_linked_shape action 에는 linked_shape_ids 목록이 필요합니다.")

        shape_ids = [self._required_shape_id(shape_id) for shape_id in linked_shape_ids]
        if not shape_ids:
            raise ValueError("resize_linked_shape action 에는 linked_shape_ids 목록이 필요합니다.")
        return shape_ids

    def _geometry_updates(self, action: Mapping[str, Any]) -> dict[str, int]:
        updates: dict[str, int] = {}
        for field_name in ("x_emu", "y_emu", "w_emu", "h_emu"):
            if field_name not in action:
                continue
            updates[field_name] = self._geometry_int(
                action[field_name],
                field_name,
                positive=field_name in {"w_emu", "h_emu"},
            )
        return updates

    def _linked_geometry_updates(self, action: Mapping[str, Any]) -> dict[str, int]:
        updates: dict[str, int] = {}
        for field_name in ("x_emu", "y_emu", "w_emu", "h_emu"):
            linked_field_name = f"linked_{field_name}"
            if linked_field_name in action:
                updates[field_name] = self._geometry_int(
                    action[linked_field_name],
                    linked_field_name,
                    positive=field_name in {"w_emu", "h_emu"},
                )
            elif field_name in {"w_emu", "h_emu"} and field_name in action:
                updates[field_name] = self._geometry_int(
                    action[field_name],
                    field_name,
                    positive=True,
                )
        return updates

    def _geometry_int(self, value: Any, field_name: str, *, positive: bool) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{field_name} 값은 정수여야 합니다.")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"{field_name} 값은 정수여야 합니다.")

        try:
            parsed_value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} 값은 정수여야 합니다.") from exc

        if positive and parsed_value <= 0:
            raise ValueError(f"{field_name} 값은 0보다 커야 합니다.")
        return parsed_value

    def _non_negative_geometry_int(self, value: Any, field_name: str) -> int:
        parsed_value = self._geometry_int(value, field_name, positive=False)
        if parsed_value < 0:
            raise ValueError(f"{field_name} 값은 0 이상이어야 합니다.")
        return parsed_value

    def _apply_geometry_updates(self, element: Element, updates: Mapping[str, int]) -> None:
        xfrm = self._geometry_xfrm(element)
        if xfrm is None:
            shape_id = self._get_shape_id(element) or "(unknown)"
            raise ValueError(f"shape_id {shape_id} 의 xfrm 정보를 찾을 수 없습니다.")

        if "x_emu" in updates or "y_emu" in updates:
            off = self._first_child(xfrm, self.DRAWINGML_NS, "off")
            if off is None:
                shape_id = self._get_shape_id(element) or "(unknown)"
                raise ValueError(f"shape_id {shape_id} 의 off 정보를 찾을 수 없습니다.")
            if "x_emu" in updates:
                off.setAttribute("x", str(updates["x_emu"]))
            if "y_emu" in updates:
                off.setAttribute("y", str(updates["y_emu"]))

        if "w_emu" in updates or "h_emu" in updates:
            ext = self._first_child(xfrm, self.DRAWINGML_NS, "ext")
            if ext is None:
                shape_id = self._get_shape_id(element) or "(unknown)"
                raise ValueError(f"shape_id {shape_id} 의 ext 정보를 찾을 수 없습니다.")
            if "w_emu" in updates:
                ext.setAttribute("cx", str(updates["w_emu"]))
            if "h_emu" in updates:
                ext.setAttribute("cy", str(updates["h_emu"]))

    def _geometry_xfrm(self, element: Element) -> Element | None:
        """shape 종류별 실제 geometry xfrm 요소를 반환한다."""
        if element.namespaceURI == self.PML_NS and element.localName == "graphicFrame":
            return self._first_child(element, self.PML_NS, "xfrm")

        shape_properties = self._first_child(element, self.PML_NS, "spPr")
        return self._first_child(shape_properties, self.DRAWINGML_NS, "xfrm")

    def _required_current_geometry(self, element: Element, field_name: str) -> int:
        coordinates = self._coordinates(element)
        value = coordinates.get(field_name)
        if value is None:
            shape_id = self._get_shape_id(element) or "(unknown)"
            raise ValueError(f"shape_id {shape_id} 의 {field_name} 정보를 찾을 수 없습니다.")
        return value

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
        is_title_placeholder = self._is_title_placeholder(sp_element)
        if tx_body is None and not is_title_placeholder:
            return None

        current_text = self._text_body_content(tx_body) if tx_body is not None else ""
        if not current_text.strip() and not is_title_placeholder:
            return None

        kind = "image" if self._has_image_fill(sp_element) else "text"

        return {
            "shape_id": shape_id,
            "shape_name": cnv_pr.getAttribute("name") if cnv_pr is not None else "",
            **self._coordinates(sp_element),
            "current_text": current_text,
            "is_title_placeholder": is_title_placeholder,
            "font_size_pt": self._first_font_size_pt(tx_body) if tx_body is not None else None,
            "kind": kind,
            "role": "title" if is_title_placeholder else "body",
            "editable": True,
            "required": True,
            "allowed_actions": list(self._TEXT_ALLOWED_ACTIONS),
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
            "role": "chart",
            "editable": True,
            "required": True,
            "allowed_actions": list(self._CHART_ALLOWED_ACTIONS),
            "chart_rel_id": self._chart_rel_id(chart),
            "chart_type": chart_summary.get("chart_type"),
            "categories": chart_summary.get("categories", []),
            "series": chart_summary.get("series", []),
        }

    def _replace_text(self, sp_element: Element, fill: dict[str, Any]) -> list[str]:
        """도형 내부 텍스트를 교체하되 첫 pPr/rPr 서식을 보존한다."""
        doc = sp_element.ownerDocument
        tx_body = self._first_descendant(sp_element, self.PML_NS, "txBody")
        if tx_body is None:
            tx_body = self._create_text_body(doc)
            sp_element.appendChild(tx_body)

        if fill.get("text_replacement_mode") == "marker_runs":
            warnings = self._replace_marker_runs(sp_element, tx_body, fill)
            if warnings is not None:
                return warnings

        base_p_pr = self._extract_paragraph_props(tx_body)
        base_r_pr = self._extract_run_props(tx_body, doc)

        if fill.get("font_size_override") is not None:
            size_val = str(int(round(float(fill["font_size_override"]) * 100)))
            base_r_pr.setAttribute("sz", size_val)

        if fill.get("is_title"):
            base_r_pr.setAttribute("b", "1")

        warnings = self._apply_text_output_color(sp_element, base_r_pr, fill)

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

        return warnings

    def _replace_marker_runs(
        self,
        sp_element: Element,
        tx_body: Element,
        fill: dict[str, Any],
    ) -> list[str] | None:
        """mixed-color shape에서 #FF0000 marker run만 생성 텍스트로 교체한다."""
        text = str(fill.get("text", ""))
        marker_applied = False
        warnings: list[str] = []

        for paragraph in self._children(tx_body, self.DRAWINGML_NS, "p"):
            for run in self._children(paragraph, self.DRAWINGML_NS, "r"):
                run_props = self._first_child(run, self.DRAWINGML_NS, "rPr")
                if run_props is None or not self._is_marker_run(run_props, fill):
                    continue

                if marker_applied:
                    self._replace_run_text(run, "")
                    continue

                self._apply_text_style_overrides(run_props, fill)
                warnings.extend(self._apply_text_output_color(sp_element, run_props, fill))
                self._replace_run_text(run, text)
                marker_applied = True

        if not marker_applied:
            return None
        return warnings

    def _apply_text_style_overrides(self, run_props: Element, fill: Mapping[str, Any]) -> None:
        if fill.get("font_size_override") is not None:
            size_val = str(int(round(float(fill["font_size_override"]) * 100)))
            run_props.setAttribute("sz", size_val)
        if fill.get("is_title"):
            run_props.setAttribute("b", "1")

    def _replace_run_text(self, run: Element, value: str) -> None:
        text_nodes = self._children(run, self.DRAWINGML_NS, "t")
        if text_nodes:
            target = text_nodes[0]
            for extra in text_nodes[1:]:
                run.removeChild(extra)
        else:
            target = run.ownerDocument.createElementNS(self.DRAWINGML_NS, "a:t")
            run.appendChild(target)

        target.setAttribute("xml:space", "preserve")
        self._replace_text_node(target, value)

    def _is_marker_run(self, run_props: Element, fill: Mapping[str, Any]) -> bool:
        marker_color = fill.get("marker_color")
        marker_rgb = (
            self._normalize_rgb_color(marker_color, "marker_color")
            if marker_color
            else self._EXACT_MARKER_RGB
        )
        return self._run_srgb_color(run_props) == marker_rgb

    def _apply_text_output_color(
        self,
        sp_element: Element,
        run_props: Element,
        fill: Mapping[str, Any],
    ) -> list[str]:
        output_text_color = fill.get("output_text_color")
        if output_text_color:
            self._set_run_srgb_color(
                run_props,
                self._normalize_rgb_color(output_text_color, "output_text_color"),
            )
            return []

        if not self._requires_marker_color_replacement(run_props, fill):
            return []

        self._set_run_srgb_color(run_props, self._OUTPUT_TEXT_COLOR_FALLBACK_RGB)
        shape_id = self._get_shape_id(sp_element) or "(unknown)"
        return [
            "shape_id "
            f"{shape_id}의 output_text_color가 없어 "
            f"#{self._OUTPUT_TEXT_COLOR_FALLBACK_RGB}을 사용합니다."
        ]

    def _requires_marker_color_replacement(
        self,
        run_props: Element,
        fill: Mapping[str, Any],
    ) -> bool:
        marker_color = fill.get("marker_color")
        if marker_color:
            return self._normalize_rgb_color(marker_color, "marker_color") == self._EXACT_MARKER_RGB

        return self._run_srgb_color(run_props) == self._EXACT_MARKER_RGB

    def _run_srgb_color(self, run_props: Element) -> str | None:
        color = self._first_descendant(run_props, self.DRAWINGML_NS, "srgbClr")
        if color is None:
            return None
        value = color.getAttribute("val").strip().upper()
        return value or None

    def _set_run_srgb_color(self, run_props: Element, rgb_color: str) -> None:
        doc = run_props.ownerDocument
        solid_fill = self._first_child(run_props, self.DRAWINGML_NS, "solidFill")
        if solid_fill is None:
            solid_fill = doc.createElementNS(self.DRAWINGML_NS, "a:solidFill")
            run_props.insertBefore(solid_fill, run_props.firstChild)

        for child in list(solid_fill.childNodes):
            solid_fill.removeChild(child)

        srgb_color = doc.createElementNS(self.DRAWINGML_NS, "a:srgbClr")
        srgb_color.setAttribute("val", rgb_color)
        solid_fill.appendChild(srgb_color)

    def _normalize_rgb_color(self, value: Any, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} 값은 #RRGGBB 형식이어야 합니다.")

        color = value.strip().removeprefix("#")
        if len(color) != 6 or any(char not in self._HEX_DIGITS for char in color):
            raise ValueError(f"{field_name} 값은 #RRGGBB 형식이어야 합니다.")
        return color.upper()

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
            lines.append(self._text_with_breaks(paragraph))
        return "\n".join(lines)

    def _text_with_breaks(self, element: Element) -> str:
        parts = []
        for child in element.childNodes:
            if child.nodeType != Node.ELEMENT_NODE:
                continue

            if child.namespaceURI == self.DRAWINGML_NS and child.localName == "br":
                parts.append("\n")
            elif child.namespaceURI == self.DRAWINGML_NS and child.localName == "t":
                parts.append(self._node_text(child))
            else:
                parts.append(self._text_with_breaks(child))
        return "".join(parts)

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

        for tag_name in ("defRPr", "endParaRPr"):
            fallback_props = self._first_descendant(tx_body, self.DRAWINGML_NS, tag_name)
            if fallback_props is not None:
                return self._clone_as_run_props(fallback_props, doc)

        return doc.createElementNS(self.DRAWINGML_NS, "a:rPr")

    def _clone_as_run_props(self, source_props: Element, doc: Document) -> Element:
        run_props = doc.createElementNS(self.DRAWINGML_NS, "a:rPr")
        for index in range(source_props.attributes.length):
            attr = source_props.attributes.item(index)
            if attr.namespaceURI:
                run_props.setAttributeNS(attr.namespaceURI, attr.name, attr.value)
            else:
                run_props.setAttribute(attr.name, attr.value)

        for child in source_props.childNodes:
            run_props.appendChild(child.cloneNode(deep=True))
        return run_props

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
                if relationship.getAttribute("TargetMode") == "External":
                    raise ValueError("외부 차트 관계는 지원하지 않습니다.")
                return relationship.getAttribute("Target") or None

        return None

    def _resolve_part_path(self, source_xml_path: Path, target: str) -> Path:
        normalized_target = target.replace("\\", "/")
        package_root = self._package_root_for_part(source_xml_path)
        if normalized_target.startswith("/"):
            candidate = package_root / normalized_target.lstrip("/")
        else:
            candidate = source_xml_path.parent / normalized_target

        resolved = candidate.resolve()
        if not resolved.is_relative_to(package_root):
            raise ValueError("차트 대상 경로가 패키지 범위를 벗어났습니다.")
        return resolved

    def _package_root_for_part(self, part_path: Path) -> Path:
        resolved_part_path = part_path.resolve()
        for parent in (resolved_part_path.parent, *resolved_part_path.parents):
            if parent.name == "ppt":
                return parent.parent.resolve()

        if len(resolved_part_path.parents) >= 3:
            return resolved_part_path.parents[2].resolve()
        return resolved_part_path.parent.resolve()

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
