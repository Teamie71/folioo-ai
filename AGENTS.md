# AGENTS.md - AI 코딩 에이전트 가이드

이 문서는 folioo-ai 코드베이스에서 작업하는 AI 에이전트를 위한 가이드입니다.

## 프로젝트 개요

- **언어**: Python 3.12+
- **프레임워크**: LangGraph (멀티 에이전트), FastAPI (웹), LangChain (LLM 통합)
- **패키지 매니저**: uv
- **LLM 제공자**: OpenRouter (via LangChain ChatOpenAI)

## 디렉토리 구조

```
folioo-ai/
├── app/                    # FastAPI 애플리케이션
│   ├── api/v1/             # API v1 엔드포인트
│   ├── db/migrations/      # 데이터베이스 마이그레이션
│   ├── models/             # 데이터베이스 모델
│   └── schemas/            # Pydantic 스키마
├── common/                 # 공유 유틸리티
│   ├── llm/                # LLM 클라이언트 (OpenRouter)
│   ├── utils/              # 공통 유틸리티
│   └── vector_store/       # 벡터 스토어
├── features/               # 기능별 모듈
│   └── interview/          # 인터뷰 에이전트
│       ├── agents/         # LangGraph 에이전트
│       │   ├── nodes/      # 에이전트 노드
│       │   └── prompts/    # 프롬프트 템플릿
│       └── config/         # 설정 파일 (YAML)
└── tests/                  # 테스트 (features 구조 미러링)
```

## 빌드/린트/테스트 명령어

### 의존성 설치

```bash
uv sync              # 기본 의존성
uv sync --dev        # 개발 의존성 포함
```

### 린트 및 포맷팅

```bash
ruff check .                   # 린트 검사
ruff check --fix .             # 린트 검사 + 자동 수정
ruff format .                  # 코드 포맷팅
ruff format --check .          # 포맷 검사만
pre-commit run --all-files     # 모든 pre-commit 훅 실행
```

### 테스트 실행

```bash
# 전체 테스트
pytest

# 단일 테스트 파일
pytest tests/test_features/test_interview/test_config.py

# 특정 테스트 함수
pytest tests/test_features/test_interview/test_config.py::test_load_stage_config_stage_1

# 패턴으로 테스트 선택
pytest -k "test_load_stage"

# 상세 출력
pytest -v

# 첫 실패시 중단
pytest -x
```

### 실행

```bash
python main.py                 # 메인 앱 실행
langgraph dev                  # LangGraph 개발 서버
```

## 코드 스타일 가이드라인

### 임포트 순서

Ruff isort 설정에 따라 정렬됩니다:

```python
# 1. 표준 라이브러리
import os
from functools import lru_cache

# 2. 서드파티 라이브러리
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# 3. 퍼스트파티 (app, features, common)
from common.llm.client import get_llm
from features.interview.agents import InterviewState
```

### 네이밍 컨벤션

| 항목 | 스타일 | 예시 |
|------|--------|------|
| 파일/모듈 | snake_case | `question_generator.py` |
| 클래스 | PascalCase | `InterviewState`, `StageConfig` |
| 함수/변수 | snake_case | `load_stage_config`, `current_stage` |
| 상수 | UPPER_SNAKE_CASE | `MAX_RETRIES`, `DEFAULT_MODEL` |
| 프라이빗 | _prefix | `_internal_helper` |

### 타입 힌트

Python 3.12+ 문법 사용 (typing 모듈 임포트 최소화):

```python
# 권장 (Python 3.12+)
def process(items: list[str]) -> dict[str, int]:
    data: str | None = None
    ...

# 비권장 (레거시)
from typing import List, Dict, Optional
def process(items: List[str]) -> Dict[str, int]:
    data: Optional[str] = None
```

### 독스트링

Google 스타일, 한국어 사용:

```python
"""모듈 설명 (한 줄)"""

def get_llm(
    model: str | None = None,
    temperature: float = 0.7,
) -> ChatOpenAI:
    """
    OpenRouter 기반 LLM 클라이언트 반환

    Args:
        model: 사용할 모델명 (기본값: 환경변수 LLM_MODEL)
        temperature: 생성 다양성 (0.0 ~ 1.0)

    Returns:
        ChatOpenAI: LangChain 호환 LLM 클라이언트

    Raises:
        ValueError: API 키가 설정되지 않은 경우
    """
```

### 에러 처리

명확한 에러 메시지와 함께 예외 발생 (한국어):

```python
if not api_key:
    raise ValueError("OPENROUTER_API_KEY 환경변수가 설정되지 않았습니다.")

if stage not in [1, 2, 3, 4]:
    raise ValueError(f"Invalid stage: {stage}. Must be 1-4.")
```

### LangGraph 노드 패턴

각 노드는 일관된 패턴을 따릅니다:

```python
def run(state: InterviewState) -> InterviewState:
    """노드 설명"""
    # 로직 처리
    return {
        **state,
        "updated_field": new_value,
        "next_node": "next_node_name",
    }
```

### TypedDict 상태 정의

```python
from typing import Annotated, Literal, TypedDict
from langgraph.graph.message import add_messages

class InterviewState(TypedDict):
    """공유 상태 정의"""
    user_id: str
    messages: Annotated[list, add_messages]  # 리듀서 사용
    current_stage: Literal[1, 2, 3, 4]
    collected_data: dict
```

### 모듈 구조

- `__init__.py`에서 명시적 익스포트
- `__all__` 정의로 퍼블릭 API 선언

```python
# features/interview/agents/__init__.py
from .graph import build_graph
from .state import InterviewState

__all__ = ["build_graph", "InterviewState"]
```

## 테스트 작성 가이드

### 테스트 파일 구조

`tests/` 디렉토리는 `features/` 구조를 미러링합니다:

```
features/interview/config/loader.py
→ tests/test_features/test_interview/test_config.py
```

### 테스트 패턴

```python
"""설정 로더 테스트"""

import pytest
from features.interview.config.loader import load_stage_config, StageConfig


def test_load_stage_config_stage_1():
    """1단계 설정 로드 테스트"""
    config = load_stage_config(1)
    assert isinstance(config, StageConfig)
    assert config.name == "프로젝트 개요 및 구조화"


@pytest.fixture
def initial_state() -> InterviewState:
    """테스트용 초기 상태 fixture"""
    return {"user_id": "test", "messages": [], ...}


@pytest.mark.parametrize("stage", [1, 2, 3, 4])
def test_all_stages_valid(stage):
    """파라미터화된 테스트"""
    config = load_stage_config(stage)
    assert config.name
```

## 설정 파일 참조

| 파일 | 용도 |
|------|------|
| `pyproject.toml` | 프로젝트 메타데이터, 의존성, Ruff/pytest 설정 |
| `.pre-commit-config.yaml` | Pre-commit 훅 (ruff, trailing-whitespace 등) |
| `langgraph.json` | LangGraph Studio 설정 |
| `.env` | 환경변수 (OPENROUTER_API_KEY, LLM_MODEL_NAME) |

## 주요 Ruff 규칙

- 라인 길이: 100자
- 쿼트 스타일: 쌍따옴표 (`"`)
- 들여쓰기: 스페이스 4칸
- 규칙: E (pycodestyle), W (warnings), F (pyflakes), I (isort), N (pep8-naming), UP (pyupgrade)

## 커밋 전 체크리스트

1. `ruff check --fix .` 실행
2. `ruff format .` 실행
3. `pytest` 실행하여 테스트 통과 확인
4. 새 기능에 대한 테스트 추가
