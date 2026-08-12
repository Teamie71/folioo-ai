"""OpenRouter LLM 클라이언트"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()


def _build_llm(
    model: str | None = None,
    temperature: float = 0.7,
    timeout: float | None = None,
    *,
    disable_streaming: bool = False,
    max_retries: int | None = None,
) -> ChatOpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    default_model = os.getenv("LLM_MODEL_NAME", "openai/gpt-oss-120b")

    if not api_key:
        raise ValueError("OPENROUTER_API_KEY 환경변수가 설정되지 않았습니다.")

    llm_kwargs = {
        "model": model or default_model,
        "openai_api_key": api_key,
        "openai_api_base": base_url,
        "temperature": temperature,
        "request_timeout": timeout,
        "disable_streaming": disable_streaming,
    }
    if max_retries is not None:
        llm_kwargs["max_retries"] = max_retries

    return ChatOpenAI(
        **llm_kwargs,
    )


@lru_cache(maxsize=8)
def get_llm(
    model: str | None = None,
    temperature: float = 0.7,
    timeout: float | None = None,
) -> ChatOpenAI:
    """
    OpenRouter 기반 LLM 클라이언트 반환 (캐시됨)

    Args:
        model: 사용할 모델명 (기본값: 환경변수 LLM_MODEL)
        temperature: 생성 다양성 (0.0 ~ 1.0)
        timeout: 요청 타임아웃(초). None이면 라이브러리 기본값 사용

    Returns:
        ChatOpenAI: LangChain 호환 LLM 클라이언트
    """
    return _build_llm(model=model, temperature=temperature, timeout=timeout)


@lru_cache(maxsize=8)
def get_analyst_llm(
    model: str | None = None,
    temperature: float = 0.3,
) -> ChatOpenAI:
    """Analyst 노드 전용 LLM 클라이언트 반환"""

    return _build_llm(
        model=model,
        temperature=temperature,
        timeout=120,
        disable_streaming=True,
        max_retries=0,
    )


@lru_cache(maxsize=8)
def get_experience_map_llm(
    model: str | None = None,
    temperature: float = 0.0,
    timeout: float = 60,
) -> ChatOpenAI:
    """경험정리 노드 전용 LLM 클라이언트 반환

    `max_retries=0` 으로 고정한다. 자동 재시도는 LangGraph `RetryPolicy` 한 곳에서만
    관리해야 하며, 클라이언트가 따로 재시도하면 노드 실패 1회가 실제로는 여러 번의
    LLM 호출이 된다 (에이전트 문서 7-2).

    분류·구조화가 주 용도라 `temperature` 기본값은 0이다. 같은 입력에 같은 판정이
    나오는 편이 디버깅에 유리하다.
    """
    return _build_llm(
        model=model,
        temperature=temperature,
        timeout=timeout,
        disable_streaming=True,
        max_retries=0,
    )


@lru_cache(maxsize=4)
def get_file_processor_llm(
    model: str | None = None,
    temperature: float = 0.0,
) -> ChatOpenAI:
    """FileProcessor 노드 전용 Vision LLM 클라이언트 반환"""

    return _build_llm(
        model=_file_processor_model(model),
        temperature=temperature,
        timeout=120,
        disable_streaming=True,
        max_retries=0,
    )


def get_file_processor_llm_uncached(
    model: str | None = None,
    temperature: float = 0.0,
) -> ChatOpenAI:
    """FileProcessor 노드 전용 Vision LLM 클라이언트를 캐시 없이 반환"""

    return _build_llm(
        model=_file_processor_model(model),
        temperature=temperature,
        timeout=120,
        disable_streaming=True,
        max_retries=0,
    )


def get_llm_uncached(
    model: str | None = None,
    temperature: float = 0.7,
    timeout: float | None = None,
) -> ChatOpenAI:
    """캐싱 없이 새 LLM 인스턴스 반환 (테스트/특수 케이스용)"""

    return _build_llm(model=model, temperature=temperature, timeout=timeout)


def _file_processor_model(model: str | None) -> str:
    return model or os.getenv("FILE_PROCESSOR_MODEL_NAME", "google/gemini-3.1-pro-preview")
