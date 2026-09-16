"""경험정리 수동 테스트 UI 테스트"""

from app.experience_map_test_ui import TEST_PAGE_HTML


def test_test_ui_generates_request_id_without_web_crypto():
    """HTTP Tailscale 접속에서도 전송 요청 ID를 만들 수 있어야 한다."""
    assert "globalThis.crypto?.randomUUID" in TEST_PAGE_HTML
    assert "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx" in TEST_PAGE_HTML


def test_test_ui_places_large_map_before_agent_and_has_no_prefilled_story():
    """왼쪽 큰 맵이 먼저 나오고 입력·대화에 샘플 경험을 미리 넣지 않는다."""
    map_heading = TEST_PAGE_HTML.index("<h2>경험 맵</h2>")
    agent_heading = TEST_PAGE_HTML.index("<h2>경험정리 에이전트</h2>")

    assert map_heading < agent_heading
    assert "grid-template-columns: minmax(620px, 1.55fr)" in TEST_PAGE_HTML
    assert "height: max(620px, calc(100vh - 300px))" in TEST_PAGE_HTML
    assert "행사 신청 페이지의 이탈률이 높았다" not in TEST_PAGE_HTML
    assert '<div id="chatHistory"></div>' in TEST_PAGE_HTML
    assert (
        '<textarea id="message" placeholder="정리할 경험의 사실을 처음부터 입력하세요."></textarea>'
        in TEST_PAGE_HTML
    )


def test_test_ui_treats_sse_error_and_incomplete_eof_as_failure():
    """오류 이벤트나 완료 이벤트 없는 종료를 성공으로 표시하지 않는다."""
    assert "await reader.cancel(); throw new Error(payload.error.message);" in TEST_PAGE_HTML
    assert "terminalEvent !== 'processing_complete'" in TEST_PAGE_HTML
