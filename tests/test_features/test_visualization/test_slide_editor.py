"""SlideEditor XML 편집 테스트."""

from pathlib import Path
from xml.dom.minidom import Document, Element

import pytest
from defusedxml.minidom import parse

from features.visualization.pptx import SlideEditor

PML_NS = SlideEditor.PML_NS
DRAWINGML_NS = SlideEditor.DRAWINGML_NS
CHART_NS = SlideEditor.CHART_NS


def _make_sample_package(tmp_path: Path) -> tuple[Path, Path]:
    """슬라이드 XML, rels, chart XML 을 포함한 최소 PPTX unpack 구조를 만든다."""
    slides_dir = tmp_path / "ppt" / "slides"
    rels_dir = slides_dir / "_rels"
    charts_dir = tmp_path / "ppt" / "charts"
    rels_dir.mkdir(parents=True)
    charts_dir.mkdir(parents=True)

    slide_path = slides_dir / "slide1.xml"
    chart_path = charts_dir / "chart1.xml"

    slide_path.write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="2" name="Designer Renamed Title"/>
          <p:cNvSpPr/>
          <p:nvPr><p:ph type="title"/></p:nvPr>
        </p:nvSpPr>
        <p:spPr>
          <a:xfrm>
            <a:off x="685800" y="457200"/>
            <a:ext cx="7772400" cy="914400"/>
          </a:xfrm>
          <a:gradFill>
            <a:gsLst><a:gs pos="0"><a:srgbClr val="FFFFFF"/></a:gs></a:gsLst>
          </a:gradFill>
          <a:effectLst><a:outerShdw blurRad="38100"/></a:effectLst>
        </p:spPr>
        <p:txBody>
          <a:bodyPr/>
          <a:lstStyle/>
          <a:p>
            <a:pPr algn="ctr"/>
            <a:r>
              <a:rPr lang="ko-KR" sz="4000" b="0">
                <a:solidFill><a:srgbClr val="123456"/></a:solidFill>
                <a:latin typeface="Pretendard"/>
              </a:rPr>
              <a:t>여기에 프로젝트명</a:t>
            </a:r>
          </a:p>
        </p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="3" name="Body Shape"/>
          <p:cNvSpPr/>
          <p:nvPr/>
        </p:nvSpPr>
        <p:spPr>
          <a:xfrm>
            <a:off x="914400" y="1600200"/>
            <a:ext cx="5486400" cy="1371600"/>
          </a:xfrm>
        </p:spPr>
        <p:txBody>
          <a:bodyPr/>
          <a:lstStyle/>
          <a:p>
            <a:pPr algn="l"/>
            <a:r><a:rPr sz="1800"/><a:t>삭제될 본문</a:t></a:r>
          </a:p>
        </p:txBody>
      </p:sp>
      <p:graphicFrame>
        <p:nvGraphicFramePr>
          <p:cNvPr id="8" name="Main Chart"/>
          <p:cNvGraphicFramePr/>
          <p:nvPr/>
        </p:nvGraphicFramePr>
        <p:xfrm>
          <a:off x="1524000" y="3200400"/>
          <a:ext cx="6096000" cy="2743200"/>
        </p:xfrm>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">
            <c:chart r:id="rId7"/>
          </a:graphicData>
        </a:graphic>
      </p:graphicFrame>
    </p:spTree>
  </p:cSld>
</p:sld>
""",
        encoding="utf-8",
    )

    (rels_dir / "slide1.xml.rels").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId7"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
                Target="../charts/chart1.xml"/>
</Relationships>
""",
        encoding="utf-8",
    )

    chart_path.write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <c:chart>
    <c:title>
      <c:tx><c:rich><a:p><a:r><a:t>기존 차트</a:t></a:r></a:p></c:rich></c:tx>
    </c:title>
    <c:plotArea>
      <c:barChart>
        <c:barDir val="col"/>
        <c:ser>
          <c:idx val="0"/>
          <c:order val="0"/>
          <c:tx>
            <c:strRef>
              <c:f>Sheet1!$B$1</c:f>
              <c:strCache>
                <c:ptCount val="1"/>
                <c:pt idx="0"><c:v>기존</c:v></c:pt>
              </c:strCache>
            </c:strRef>
          </c:tx>
          <c:cat>
            <c:strRef>
              <c:f>Sheet1!$A$2:$A$3</c:f>
              <c:strCache>
                <c:ptCount val="2"/>
                <c:pt idx="0"><c:v>A</c:v></c:pt>
                <c:pt idx="1"><c:v>B</c:v></c:pt>
              </c:strCache>
            </c:strRef>
          </c:cat>
          <c:val>
            <c:numRef>
              <c:f>Sheet1!$B$2:$B$3</c:f>
              <c:numCache>
                <c:formatCode>General</c:formatCode>
                <c:ptCount val="2"/>
                <c:pt idx="0"><c:v>10</c:v></c:pt>
                <c:pt idx="1"><c:v>20</c:v></c:pt>
              </c:numCache>
            </c:numRef>
          </c:val>
        </c:ser>
        <c:axId val="10"/>
        <c:axId val="20"/>
      </c:barChart>
    </c:plotArea>
  </c:chart>
</c:chartSpace>
""",
        encoding="utf-8",
    )

    return slide_path, chart_path


def _node_text(element: Element) -> str:
    return "".join(child.data for child in element.childNodes if child.nodeType == child.TEXT_NODE)


def _shape_by_id(doc: Document, shape_id: str) -> Element:
    for shape in doc.getElementsByTagNameNS(PML_NS, "sp"):
        c_nv_pr = shape.getElementsByTagNameNS(PML_NS, "cNvPr")[0]
        if c_nv_pr.getAttribute("id") == shape_id:
            return shape
    raise AssertionError(f"도형을 찾을 수 없습니다: {shape_id}")


def _cache_values(cache: Element) -> list[str]:
    values = []
    for point in cache.getElementsByTagNameNS(CHART_NS, "pt"):
        values.append(_node_text(point.getElementsByTagNameNS(CHART_NS, "v")[0]))
    return values


def test_extract_slots_reads_text_and_chart_metadata(tmp_path: Path) -> None:
    """텍스트/차트 Slot 과 EMU, 폰트 크기, 차트 캐시를 추출한다."""
    slide_path, _ = _make_sample_package(tmp_path)

    slots = {slot["shape_id"]: slot for slot in SlideEditor().extract_slots(str(slide_path))}

    assert set(slots) == {"2", "3", "8"}
    assert slots["2"]["shape_name"] == "Designer Renamed Title"
    assert slots["2"]["x_emu"] == 685800
    assert slots["2"]["y_emu"] == 457200
    assert slots["2"]["w_emu"] == 7772400
    assert slots["2"]["h_emu"] == 914400
    assert slots["2"]["current_text"] == "여기에 프로젝트명"
    assert slots["2"]["is_title_placeholder"] is True
    assert slots["2"]["font_size_pt"] == 40.0
    assert slots["2"]["kind"] == "text"

    assert slots["8"]["kind"] == "chart"
    assert slots["8"]["chart_rel_id"] == "rId7"
    assert slots["8"]["chart_type"] == "bar"
    assert slots["8"]["current_text"] == "기존 차트"
    assert slots["8"]["categories"] == ["A", "B"]
    assert slots["8"]["series"] == [{"name": "기존", "values": [10, 20]}]
    assert slots["8"]["x_emu"] == 1524000
    assert slots["8"]["h_emu"] == 2743200


def test_extract_slots_preserves_soft_line_breaks(tmp_path: Path) -> None:
    """같은 문단 안의 a:br 을 current_text 줄바꿈으로 보존한다."""
    slide_path, _ = _make_sample_package(tmp_path)
    slide_xml = slide_path.read_text(encoding="utf-8").replace(
        "<a:t>여기에 프로젝트명</a:t>",
        """<a:t>첫 줄</a:t>
            </a:r>
            <a:br/>
            <a:r>
              <a:t>둘째 줄</a:t>""",
    )
    slide_path.write_text(slide_xml, encoding="utf-8")

    slots = {slot["shape_id"]: slot for slot in SlideEditor().extract_slots(str(slide_path))}

    assert slots["2"]["current_text"] == "첫 줄\n둘째 줄"


def test_extract_slots_rejects_external_chart_relationship(tmp_path: Path) -> None:
    """외부 차트 relationship 은 읽지 않는다."""
    slide_path, _ = _make_sample_package(tmp_path)
    rels_path = slide_path.parent / "_rels" / "slide1.xml.rels"
    rels_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId7"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
                Target="https://example.com/chart.xml"
                TargetMode="External"/>
</Relationships>
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="외부 차트 관계"):
        SlideEditor().extract_slots(str(slide_path))


def test_extract_slots_rejects_chart_target_outside_package(tmp_path: Path) -> None:
    """패키지 루트 밖으로 나가는 chart target 은 거부한다."""
    slide_path, _ = _make_sample_package(tmp_path)
    rels_path = slide_path.parent / "_rels" / "slide1.xml.rels"
    rels_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId7"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
                Target="../../../outside/chart1.xml"/>
</Relationships>
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="패키지 범위"):
        SlideEditor().extract_slots(str(slide_path))


def test_apply_text_preserves_shape_and_text_style_with_overrides(tmp_path: Path) -> None:
    """텍스트 교체 후 도형 서식은 보존하고 폰트 오버라이드를 반영한다."""
    slide_path, _ = _make_sample_package(tmp_path)

    SlideEditor().apply_fills(
        str(slide_path),
        {
            "2": {
                "action": "text",
                "text": "  새 제목\n두 번째 줄",
                "font_size_override": 28,
                "is_title": True,
            }
        },
    )

    doc = parse(str(slide_path))
    title_shape = _shape_by_id(doc, "2")
    shape_props = title_shape.getElementsByTagNameNS(PML_NS, "spPr")[0]
    paragraphs = title_shape.getElementsByTagNameNS(DRAWINGML_NS, "p")
    texts = title_shape.getElementsByTagNameNS(DRAWINGML_NS, "t")

    assert shape_props.getElementsByTagNameNS(DRAWINGML_NS, "gradFill")
    assert shape_props.getElementsByTagNameNS(DRAWINGML_NS, "outerShdw")
    assert [_node_text(text) for text in texts] == ["  새 제목", "두 번째 줄"]
    assert [text.getAttribute("xml:space") for text in texts] == ["preserve", "preserve"]

    for paragraph in paragraphs:
        paragraph_props = paragraph.getElementsByTagNameNS(DRAWINGML_NS, "pPr")[0]
        run_props = paragraph.getElementsByTagNameNS(DRAWINGML_NS, "rPr")[0]
        latin = run_props.getElementsByTagNameNS(DRAWINGML_NS, "latin")[0]
        color = run_props.getElementsByTagNameNS(DRAWINGML_NS, "srgbClr")[0]
        assert paragraph_props.getAttribute("algn") == "ctr"
        assert run_props.getAttribute("sz") == "2800"
        assert run_props.getAttribute("b") == "1"
        assert latin.getAttribute("typeface") == "Pretendard"
        assert color.getAttribute("val") == "123456"


def test_apply_text_uses_default_run_props_when_run_props_missing(tmp_path: Path) -> None:
    """rPr 이 없으면 defRPr 서식을 a:rPr 로 정규화해 사용한다."""
    slide_path, _ = _make_sample_package(tmp_path)
    slide_xml = slide_path.read_text(encoding="utf-8")
    slide_xml = slide_xml.replace(
        '<a:pPr algn="ctr"/>',
        """<a:pPr algn="ctr">
              <a:defRPr sz="3600">
                <a:solidFill><a:srgbClr val="ABCDEF"/></a:solidFill>
                <a:latin typeface="FallbackFont"/>
              </a:defRPr>
            </a:pPr>""",
        1,
    )
    slide_xml = slide_xml.replace(
        """              <a:rPr lang="ko-KR" sz="4000" b="0">
                <a:solidFill><a:srgbClr val="123456"/></a:solidFill>
                <a:latin typeface="Pretendard"/>
              </a:rPr>
""",
        "",
        1,
    )
    slide_path.write_text(slide_xml, encoding="utf-8")

    SlideEditor().apply_fills(
        str(slide_path),
        {"2": {"action": "text", "text": "기본 서식 유지"}},
    )

    doc = parse(str(slide_path))
    title_shape = _shape_by_id(doc, "2")
    run_props = title_shape.getElementsByTagNameNS(DRAWINGML_NS, "rPr")[0]
    latin = run_props.getElementsByTagNameNS(DRAWINGML_NS, "latin")[0]
    color = run_props.getElementsByTagNameNS(DRAWINGML_NS, "srgbClr")[0]

    assert run_props.tagName == "a:rPr"
    assert run_props.getAttribute("sz") == "3600"
    assert latin.getAttribute("typeface") == "FallbackFont"
    assert color.getAttribute("val") == "ABCDEF"


def test_apply_remove_deletes_shape_tree(tmp_path: Path) -> None:
    """remove action 은 대상 p:sp 전체를 제거한다."""
    slide_path, _ = _make_sample_package(tmp_path)

    SlideEditor().apply_fills(str(slide_path), {"3": {"action": "remove"}})

    doc = parse(str(slide_path))
    shape_ids = [
        shape.getElementsByTagNameNS(PML_NS, "cNvPr")[0].getAttribute("id")
        for shape in doc.getElementsByTagNameNS(PML_NS, "sp")
    ]

    assert shape_ids == ["2"]
    assert "삭제될 본문" not in slide_path.read_text(encoding="utf-8")


def test_clear_content_removes_visible_shapes_and_keeps_blank_slide(tmp_path: Path) -> None:
    """콘텐츠 생성 실패 시 템플릿 예시 문구가 보이지 않도록 빈 페이지로 만든다."""
    slide_path, _ = _make_sample_package(tmp_path)

    SlideEditor().clear_content(str(slide_path))

    doc = parse(str(slide_path))
    assert doc.getElementsByTagNameNS(PML_NS, "spTree")
    assert doc.getElementsByTagNameNS(PML_NS, "sp") == []
    assert doc.getElementsByTagNameNS(PML_NS, "graphicFrame") == []
    xml = slide_path.read_text(encoding="utf-8")
    assert "여기에 프로젝트명" not in xml
    assert "삭제될 본문" not in xml


def test_apply_chart_updates_cache_formulas_and_keeps_chart_type(tmp_path: Path) -> None:
    """chart action 은 캐시와 수식을 함께 갱신하고 차트 타입은 유지한다."""
    slide_path, chart_path = _make_sample_package(tmp_path)

    SlideEditor().apply_fills(
        str(slide_path),
        {
            "8": {
                "action": "chart",
                "data": {
                    "categories": ["전환율", "이탈률", "잔존율"],
                    "series": [{"name": "개선 후", "values": [148, 32, 91]}],
                },
                "font_size_override": 99,
                "is_title": True,
            }
        },
    )

    doc = parse(str(chart_path))
    bar_charts = doc.getElementsByTagNameNS(CHART_NS, "barChart")
    assert len(bar_charts) == 1
    assert not doc.getElementsByTagNameNS(CHART_NS, "lineChart")

    series = bar_charts[0].getElementsByTagNameNS(CHART_NS, "ser")[0]
    tx_cache = series.getElementsByTagNameNS(CHART_NS, "tx")[0].getElementsByTagNameNS(
        CHART_NS, "strCache"
    )[0]
    cat_cache = series.getElementsByTagNameNS(CHART_NS, "cat")[0].getElementsByTagNameNS(
        CHART_NS, "strCache"
    )[0]
    num_cache = series.getElementsByTagNameNS(CHART_NS, "numCache")[0]
    formulas = [_node_text(formula) for formula in doc.getElementsByTagNameNS(CHART_NS, "f")]

    assert _cache_values(tx_cache) == ["개선 후"]
    assert tx_cache.getElementsByTagNameNS(CHART_NS, "ptCount")[0].getAttribute("val") == "1"
    assert _cache_values(cat_cache) == ["전환율", "이탈률", "잔존율"]
    assert cat_cache.getElementsByTagNameNS(CHART_NS, "ptCount")[0].getAttribute("val") == "3"
    assert _cache_values(num_cache) == ["148", "32", "91"]
    assert num_cache.getElementsByTagNameNS(CHART_NS, "ptCount")[0].getAttribute("val") == "3"
    assert "Sheet1!$B$1" in formulas
    assert "Sheet1!$A$2:$A$4" in formulas
    assert "Sheet1!$B$2:$B$4" in formulas
