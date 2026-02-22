"""포트폴리오 프롬프트 입력 포맷터"""

import json


def format_collected_data_for_prompt(collected_data: dict) -> str:
    """collected_data를 프롬프트 입력용 문자열로 변환"""
    if not collected_data:
        return "수집된 데이터 없음"
    return json.dumps(collected_data, ensure_ascii=False, indent=2, default=str)


__all__ = ["format_collected_data_for_prompt"]
