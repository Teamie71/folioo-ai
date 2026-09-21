"""Gemini LLM 클라이언트

기본은 Gemini Developer API(`GEMINI_API_KEY`)를 쓴다. `GOOGLE_GENAI_USE_VERTEXAI=true`로
같은 API 키를 Vertex AI Express 모드로 전환할 수 있다 (예: `vertex-express@` 서비스
계정에 묶인 API 키).

주의: Express 모드(API 키)를 쓸 때는 `GOOGLE_CLOUD_PROJECT`를 **설정하면 안 된다**.
`google-genai` SDK가 project가 있으면 API 키를 무시하고 ADC(서비스 계정/gcloud 로그인)
인증으로 전환해버려, API 키만 있고 ADC가 없는 환경에서는
`DefaultCredentialsError`로 실패한다. 서비스 계정 자격증명으로 완전한 Vertex AI를 쓰려면
API 키를 빼고 `GOOGLE_CLOUD_PROJECT` + ADC(`gcloud auth application-default login`
또는 서비스 계정 키)를 쓴다.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

DEFAULT_MODEL_NAME = "gemini-3.1-flash-lite"


def _build_llm(
    model: str | None = None,
    temperature: float = 0.7,
    timeout: float | None = None,
    *,
    disable_streaming: bool = False,
    max_retries: int | None = None,
    max_tokens: int | None = None,
) -> ChatGoogleGenerativeAI:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    default_model = os.getenv("LLM_MODEL_NAME", DEFAULT_MODEL_NAME)
    use_vertexai = os.getenv("GOOGLE_GENAI_USE_VERTEXAI")

    if not api_key and not use_vertexai:
        raise ValueError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")

    llm_kwargs = {
        "model": model or default_model,
        "temperature": temperature,
        "request_timeout": timeout,
        "disable_streaming": disable_streaming,
    }
    if api_key:
        llm_kwargs["api_key"] = api_key
    if max_retries is not None:
        llm_kwargs["max_retries"] = max_retries
    if max_tokens is not None:
        llm_kwargs["max_tokens"] = max_tokens

    return ChatGoogleGenerativeAI(
        **llm_kwargs,
    )


@lru_cache(maxsize=8)
def get_llm(
    model: str | None = None,
    temperature: float = 0.7,
    timeout: float | None = None,
) -> ChatGoogleGenerativeAI:
    """
    Gemini 기반 LLM 클라이언트 반환 (캐시됨)

    Args:
        model: 사용할 모델명 (기본값: 환경변수 LLM_MODEL_NAME)
        temperature: 생성 다양성 (0.0 ~ 1.0)
        timeout: 요청 타임아웃(초). None이면 라이브러리 기본값 사용

    Returns:
        ChatGoogleGenerativeAI: LangChain 호환 LLM 클라이언트
    """
    return _build_llm(model=model, temperature=temperature, timeout=timeout)


@lru_cache(maxsize=8)
def get_analyst_llm(
    model: str | None = None,
    temperature: float = 0.3,
) -> ChatGoogleGenerativeAI:
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
) -> ChatGoogleGenerativeAI:
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


FILE_PROCESSOR_MAX_TOKENS = 16384
"""OCR 출력 상한. 지정하지 않으면 provider 기본값(예: 65536)을 그대로 요청해,
계정 잔여 크레딧이 그 최대치를 못 감당하면 실제로 쓸 토큰이 훨씬 적어도
402(크레딧 부족)로 통째로 거부된다. 추출 텍스트는 어차피
`MAX_FILE_TEXT_CHARS`(40,000자)에서 다시 자르므로 이 정도면 충분하다."""


@lru_cache(maxsize=4)
def get_file_processor_llm(
    model: str | None = None,
    temperature: float = 0.0,
) -> ChatGoogleGenerativeAI:
    """FileProcessor 노드 전용 Vision LLM 클라이언트 반환"""

    return _build_llm(
        model=_file_processor_model(model),
        temperature=temperature,
        timeout=120,
        disable_streaming=True,
        max_retries=0,
        max_tokens=FILE_PROCESSOR_MAX_TOKENS,
    )


def get_file_processor_llm_uncached(
    model: str | None = None,
    temperature: float = 0.0,
) -> ChatGoogleGenerativeAI:
    """FileProcessor 노드 전용 Vision LLM 클라이언트를 캐시 없이 반환"""

    return _build_llm(
        model=_file_processor_model(model),
        temperature=temperature,
        timeout=120,
        disable_streaming=True,
        max_retries=0,
        max_tokens=FILE_PROCESSOR_MAX_TOKENS,
    )


def get_llm_uncached(
    model: str | None = None,
    temperature: float = 0.7,
    timeout: float | None = None,
) -> ChatGoogleGenerativeAI:
    """캐싱 없이 새 LLM 인스턴스 반환 (테스트/특수 케이스용)"""

    return _build_llm(model=model, temperature=temperature, timeout=timeout)


def _file_processor_model(model: str | None) -> str:
    return model or os.getenv("FILE_PROCESSOR_MODEL_NAME", DEFAULT_MODEL_NAME)
