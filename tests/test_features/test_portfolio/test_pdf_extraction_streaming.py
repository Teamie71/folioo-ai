"""활동 단위 부분 JSON 파서 테스트"""

import json

from features.portfolio.pdf_extraction.streaming import ActivityJsonStreamParser


def _activity(name: str) -> dict:
    return {
        "activity_name": name,
        "detail": ["상세"],
        "responsibility": ["담당"],
        "problem_solving": [],
        "learning": ["배운 점"],
    }


def _payload(*names: str) -> str:
    return json.dumps({"activities": [_activity(name) for name in names]}, ensure_ascii=False)


def test_yields_activity_as_soon_as_it_closes():
    """원소의 중괄호가 닫히는 순간 그 원소만 떼어 낸다."""
    parser = ActivityJsonStreamParser()
    payload = _payload("Alpha", "Beta")
    split_at = payload.index("Beta")

    first = parser.feed(payload[:split_at])
    second = parser.feed(payload[split_at:])

    assert [item["activity_name"] for item in first] == ["Alpha"]
    assert [item["activity_name"] for item in second] == ["Beta"]


def test_handles_one_character_at_a_time():
    """한 글자씩 들어와도 원소 순서와 개수가 유지된다."""
    parser = ActivityJsonStreamParser()
    collected: list[dict] = []

    for char in _payload("Alpha", "Beta", "Gamma"):
        collected.extend(parser.feed(char))

    assert [item["activity_name"] for item in collected] == ["Alpha", "Beta", "Gamma"]


def test_ignores_braces_inside_strings():
    """문자열 안의 중괄호를 원소 경계로 오인하지 않는다."""
    parser = ActivityJsonStreamParser()
    activity = _activity("Alpha")
    activity["detail"] = ["중괄호 { 와 } 가 섞인 원문", '따옴표 \\" 이스케이프']
    payload = json.dumps({"activities": [activity]}, ensure_ascii=False)

    collected = parser.feed(payload)

    assert len(collected) == 1
    assert collected[0]["detail"] == activity["detail"]


def test_ignores_nested_objects():
    """중첩 객체(problem_solving)는 원소 경계로 세지 않는다."""
    parser = ActivityJsonStreamParser()
    activity = _activity("Alpha")
    activity["problem_solving"] = [
        {"no": 1, "situation": "상황", "strategy": "전략", "reason": "이유"},
        {"no": 2, "situation": "상황", "strategy": "전략", "reason": "이유"},
    ]
    payload = json.dumps({"activities": [activity]}, ensure_ascii=False)

    collected = parser.feed(payload)

    assert [item["activity_name"] for item in collected] == ["Alpha"]
    assert len(collected[0]["problem_solving"]) == 2


def test_stops_after_array_closes():
    """배열이 닫힌 뒤 따라오는 텍스트는 원소로 잡지 않는다."""
    parser = ActivityJsonStreamParser()
    payload = _payload("Alpha") + '\n{"trailing": "noise"}'

    collected = parser.feed(payload)

    assert [item["activity_name"] for item in collected] == ["Alpha"]
    assert parser.feed('{"more": "noise"}') == []


def test_ignores_prefix_before_activities_key():
    """코드펜스 같은 선행 텍스트가 붙어도 배열을 찾아낸다."""
    parser = ActivityJsonStreamParser()
    payload = "```json\n" + _payload("Alpha")

    collected = parser.feed(payload)

    assert [item["activity_name"] for item in collected] == ["Alpha"]


def test_returns_empty_before_array_starts():
    """배열이 시작되기 전에는 아무것도 반환하지 않는다."""
    parser = ActivityJsonStreamParser()

    assert parser.feed('{"activi') == []
    assert parser.feed('ties": ') == []
