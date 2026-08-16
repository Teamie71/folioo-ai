"""경험정리 수동 테스트 UI 테스트"""

from app.experience_map_test_ui import TEST_PAGE_HTML


def test_test_ui_generates_request_id_without_web_crypto():
    """HTTP Tailscale 접속에서도 전송 요청 ID를 만들 수 있어야 한다."""
    assert "globalThis.crypto?.randomUUID" in TEST_PAGE_HTML
    assert "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx" in TEST_PAGE_HTML
