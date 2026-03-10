"""OpenRouter LLM 클라이언트"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()


@lru_cache(maxsize=1)
def get_llm(
    model: str | None = None,
    temperature: float = 0.7,
    timeout: float | None = None,
) -> ChatOpenAI:
    """
    OpenRouter 기반 LLM 클라이언트 반환 (싱글톤)

    Args:
        model: 사용할 모델명 (기본값: 환경변수 LLM_MODEL)
        temperature: 생성 다양성 (0.0 ~ 1.0)
        timeout: 요청 타임아웃(초). None이면 라이브러리 기본값 사용

    Returns:
        ChatOpenAI: LangChain 호환 LLM 클라이언트
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    default_model = os.getenv("LLM_MODEL_NAME", "openai/gpt-oss-120b")

    if not api_key:
        raise ValueError("OPENROUTER_API_KEY 환경변수가 설정되지 않았습니다.")

    return ChatOpenAI(
        model=model or default_model,
        openai_api_key=api_key,
        openai_api_base=base_url,
        temperature=temperature,
        request_timeout=timeout,
    )


def get_llm_uncached(
    model: str | None = None,
    temperature: float = 0.7,
    timeout: float | None = None,
) -> ChatOpenAI:
    """캐싱 없이 새 LLM 인스턴스 반환 (테스트/특수 케이스용)"""

    api_key = os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    default_model = os.getenv("LLM_MODEL_NAME", "openai/gpt-oss-120b")

    if not api_key:
        raise ValueError("OPENROUTER_API_KEY 환경변수가 설정되지 않았습니다.")

    return ChatOpenAI(
        model=model or default_model,
        openai_api_key=api_key,
        openai_api_base=base_url,
        temperature=temperature,
        request_timeout=timeout,
    )
