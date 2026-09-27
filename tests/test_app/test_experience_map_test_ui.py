"""경험정리 수동 테스트 UI 테스트"""

import jwt

from app.experience_map_test_ui import TEST_PAGE_HTML, _issue_test_ticket


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


def test_test_ui_mints_turn_ticket_before_send_and_retry():
    """세션 티켓(scope=read)이 아니라 턴마다 새로 받은 turn 티켓으로 실행한다.

    메인 서버 2026-09-27 변경 이후, 세션 생성 때 받은 티켓으로 chat/stream을
    호출하면 403 ticket_scope_forbidden이 난다 — 실제로 재현된 회귀다.
    """
    assert "mintTurnTicket(null)" in TEST_PAGE_HTML
    assert "mintTurnTicket(state.requestId)" in TEST_PAGE_HTML
    assert "Authorization: `Bearer ${turn.ticket}`" in TEST_PAGE_HTML
    # #send·#retry가 더 이상 state.ticket(scope=read)을 실행에 쓰지 않는다.
    send_start = TEST_PAGE_HTML.index("document.querySelector('#send').onclick")
    retry_start = TEST_PAGE_HTML.index("document.querySelector('#retry').onclick")
    send_body = TEST_PAGE_HTML[send_start : send_start + 900]
    retry_body = TEST_PAGE_HTML[retry_start : retry_start + 500]
    assert "authHeaders()" not in send_body
    assert "authHeaders()" not in retry_body


def test_issue_test_ticket_defaults_to_read_scope_without_rid(monkeypatch):
    """세션 생성용 기본 티켓은 조회 전용이라 턴을 실행할 수 없다."""
    monkeypatch.setenv("EXPMAP_TICKET_SECRET", "test-secret-with-32plus-bytes!!!")
    token = _issue_test_ticket("1", "sid", "200")
    claims = jwt.decode(token, options={"verify_signature": False})

    assert claims["scope"] == "read"
    assert "rid" not in claims


def test_issue_test_ticket_turn_scope_carries_given_request_id(monkeypatch):
    monkeypatch.setenv("EXPMAP_TICKET_SECRET", "test-secret-with-32plus-bytes!!!")
    token = _issue_test_ticket("1", "sid", "200", scope="turn", request_id="req-123")
    claims = jwt.decode(token, options={"verify_signature": False})

    assert claims["scope"] == "turn"
    assert claims["rid"] == "req-123"
