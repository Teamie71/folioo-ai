"""PDF 추출 프롬프트 테스트"""

import base64

from langchain_core.messages import HumanMessage, SystemMessage

from features.portfolio.pdf_extraction.prompts import build_pdf_extraction_messages


def test_build_pdf_extraction_messages_includes_filename_and_pdf_data_url():
    """멀티모달 메시지에 파일명과 base64 PDF 데이터 URL을 포함한다."""
    file_bytes = b"%PDF-1.4\nresume"
    filename = "portfolio.pdf"

    messages = build_pdf_extraction_messages(file_bytes=file_bytes, filename=filename)

    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert 'input_variables: ["ocr_text"]' not in messages[0].content
    assert "`activities` 배열의 길이는 최대 5개입니다." in messages[0].content
    assert (
        "원문 텍스트의 단어, 조사, 문장 구조를 단 한 글자도 바꾸지 않고 그대로 복사"
        in messages[0].content
    )
    assert 'activity_name: "중고 플랫폼 파워 셀러"' in messages[0].content

    human_content = messages[1].content

    assert isinstance(human_content, list)
    assert human_content[0]["type"] == "text"
    assert filename in human_content[0]["text"]
    assert human_content[1]["type"] == "file"
    assert human_content[1]["file"]["filename"] == filename
    assert human_content[1]["file"]["file_data"] == (
        "data:application/pdf;base64," + base64.b64encode(file_bytes).decode("utf-8")
    )
