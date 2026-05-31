"""영상 생성 데이터 계약."""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _to_camel(value: str) -> str:
    """snake_case 필드명을 camelCase alias 로 변환한다."""
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


VIDEO_MODEL_CONFIG = ConfigDict(
    alias_generator=_to_camel,
    populate_by_name=True,
    extra="forbid",
)


def _strip_non_empty(value: str, *, field_name: str) -> str:
    """공백만 있는 문자열을 거부하고 양끝 공백을 제거한다."""
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} 값은 비어 있을 수 없습니다.")
    return stripped


class VideoBaseModel(BaseModel):
    """영상 생성 계약 공통 Pydantic 설정."""

    model_config = VIDEO_MODEL_CONFIG


class VideoFormat(str, Enum):
    """영상 산출물 포맷."""

    MP4 = "mp4"


class VideoQuality(str, Enum):
    """렌더 품질 프리셋."""

    DRAFT = "draft"
    STANDARD = "standard"
    HIGH = "high"


class VideoAspectRatio(str, Enum):
    """영상 화면비."""

    WIDE = "16:9"
    SQUARE = "1:1"
    VERTICAL = "9:16"


class VideoJobStatus(str, Enum):
    """영상 생성 job 상태."""

    PENDING = "pending"
    EXTRACTING_HINTS = "extracting_hints"
    RENDERING = "rendering"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class VideoErrorCode(str, Enum):
    """사용자에게 노출 가능한 영상 생성 에러 코드."""

    INVALID_INPUT = "INVALID_INPUT"
    SOLUTION_PLAN_INVALID = "SOLUTION_PLAN_INVALID"
    HINT_EXTRACTION_FAILED = "HINT_EXTRACTION_FAILED"
    UNSUPPORTED_OPTION = "UNSUPPORTED_OPTION"
    RENDER_TIMEOUT = "RENDER_TIMEOUT"
    RENDER_FAILED = "RENDER_FAILED"
    STORAGE_FAILED = "STORAGE_FAILED"
    PIPELINE_FAILED = "PIPELINE_FAILED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"


class VideoErrorPayload(VideoBaseModel):
    """사용자 응답/콜백에 사용할 안전한 에러 payload."""

    error_code: VideoErrorCode = Field(..., description="안전한 에러 코드")
    message: str = Field(..., min_length=1, description="사용자 노출용 메시지")
    retryable: bool = Field(default=False, description="동일 요청 재시도 가능 여부")
    details: dict[str, str] | None = Field(default=None, description="안전한 보조 정보")


class VideoProblem(VideoBaseModel):
    """영상으로 설명할 문제 입력."""

    statement: str = Field(..., min_length=1, description="문제 본문")
    problem_id: str | None = Field(default=None, description="외부 문제 ID")
    title: str | None = Field(default=None, description="문제 제목")
    subject: str | None = Field(default=None, description="과목/도메인")
    answer: str | None = Field(default=None, description="정답 또는 기준 답안")
    metadata: dict[str, Any] = Field(default_factory=dict, description="소비처별 안전한 부가 정보")

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: str) -> str:
        """문제 본문은 공백만으로 구성될 수 없다."""
        return _strip_non_empty(value, field_name="statement")


class SolutionStep(VideoBaseModel):
    """풀이 계획의 단일 단계."""

    order: int = Field(..., ge=1, description="1부터 시작하는 풀이 단계 순서")
    title: str = Field(..., min_length=1, description="단계 제목")
    explanation: str = Field(..., min_length=1, description="단계별 풀이 설명")
    equation: str | None = Field(default=None, description="단계와 연결된 수식/표현")
    key_points: list[str] = Field(default_factory=list, description="강조할 개념/근거")
    duration_hint_seconds: float | None = Field(
        default=None,
        gt=0,
        le=120,
        description="해당 단계의 권장 설명 길이",
    )

    @field_validator("title", "explanation")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """필수 텍스트는 공백만으로 구성될 수 없다."""
        return _strip_non_empty(value, field_name="solution_step")


class SolutionPlan(VideoBaseModel):
    """힌트 추출기와 파이프라인이 공유하는 풀이 계획."""

    summary: str = Field(..., min_length=1, description="풀이 전체 요약")
    final_answer: str | None = Field(default=None, description="최종 답")
    steps: list[SolutionStep] = Field(..., min_length=1, description="순서화된 풀이 단계")
    concepts: list[str] = Field(default_factory=list, description="풀이에 필요한 핵심 개념")
    assumptions: list[str] = Field(default_factory=list, description="풀이 전제/가정")

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        """풀이 요약은 공백만으로 구성될 수 없다."""
        return _strip_non_empty(value, field_name="summary")

    @model_validator(mode="after")
    def validate_step_order(self) -> "SolutionPlan":
        """풀이 단계 순서는 1부터 끊김 없이 이어져야 한다."""
        orders = [step.order for step in self.steps]
        expected = list(range(1, len(self.steps) + 1))
        if orders != expected:
            raise ValueError("steps.order는 1부터 순서대로 연속되어야 합니다.")
        return self


class VisualizationHint(VideoBaseModel):
    """영상 파이프라인이 사용할 단일 시각화 힌트."""

    kind: Literal[
        "equation",
        "diagram",
        "graph",
        "table",
        "highlight",
        "animation",
        "text",
        "image",
        "code",
        "other",
    ] = Field(..., description="시각화 종류")
    description: str = Field(..., min_length=1, description="시각화 지시")
    hint_id: str | None = Field(default=None, description="힌트 식별자")
    step_order: int | None = Field(default=None, ge=1, description="연결된 풀이 단계")
    target: str | None = Field(default=None, description="시각화 대상 개념/수식/문장")
    priority: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="렌더링 우선순위",
    )
    duration_seconds: float | None = Field(
        default=None,
        gt=0,
        le=120,
        description="권장 노출 시간",
    )
    data: dict[str, Any] = Field(default_factory=dict, description="그래프 데이터 등 구조화 힌트")

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        """시각화 설명은 공백만으로 구성될 수 없다."""
        return _strip_non_empty(value, field_name="description")


class VideoHints(VideoBaseModel):
    """영상 소관의 시각화/나레이션 힌트 묶음."""

    visualization_hints: list[VisualizationHint] = Field(
        ...,
        min_length=1,
        description="영상 파이프라인용 시각화 힌트",
    )
    narration_tone: Literal["calm", "friendly", "concise", "encouraging"] = Field(
        default="friendly",
        description="나레이션 톤",
    )
    narration_language: str = Field(default="ko-KR", min_length=2, description="나레이션 언어")
    style_keywords: list[str] = Field(default_factory=list, description="영상 스타일 키워드")
    emphasis_terms: list[str] = Field(default_factory=list, description="강조할 용어")
    avoid_terms: list[str] = Field(default_factory=list, description="피해야 할 표현")

    @model_validator(mode="after")
    def validate_unique_hint_ids(self) -> "VideoHints":
        """명시된 hint_id 는 중복될 수 없다."""
        hint_ids = [hint.hint_id for hint in self.visualization_hints if hint.hint_id]
        duplicated = sorted({hint_id for hint_id in hint_ids if hint_ids.count(hint_id) > 1})
        if duplicated:
            raise ValueError(f"visualization_hints.hint_id 중복: {', '.join(duplicated)}")
        return self


class VideoOptions(VideoBaseModel):
    """영상 렌더링 옵션."""

    format: VideoFormat = Field(default=VideoFormat.MP4, description="산출물 포맷")
    quality: VideoQuality = Field(default=VideoQuality.STANDARD, description="렌더 품질")
    aspect_ratio: VideoAspectRatio = Field(default=VideoAspectRatio.WIDE, description="화면비")
    resolution_width: int = Field(default=1280, ge=320, le=3840, description="영상 너비")
    resolution_height: int = Field(default=720, ge=240, le=2160, description="영상 높이")
    fps: int = Field(default=30, ge=12, le=60, description="초당 프레임 수")
    max_duration_seconds: int = Field(default=180, ge=15, le=600, description="최대 영상 길이")
    target_duration_seconds: int | None = Field(
        default=None,
        ge=15,
        le=600,
        description="목표 영상 길이",
    )
    include_voiceover: bool = Field(default=True, description="음성 나레이션 포함 여부")
    include_subtitles: bool = Field(default=True, description="자막 포함 여부")
    background_music: bool = Field(default=False, description="배경음 포함 여부")
    locale: str = Field(default="ko-KR", min_length=2, description="영상 로케일")

    @model_validator(mode="after")
    def validate_duration_budget(self) -> "VideoOptions":
        """목표 길이는 최대 길이를 초과할 수 없다."""
        if (
            self.target_duration_seconds is not None
            and self.target_duration_seconds > self.max_duration_seconds
        ):
            raise ValueError("target_duration_seconds는 max_duration_seconds를 초과할 수 없습니다.")
        return self


class VideoGenerationInput(VideoBaseModel):
    """힌트 추출 전후로 공유되는 영상 생성 입력 계약."""

    problem: VideoProblem = Field(..., description="문제 입력")
    user_solution: str = Field(..., min_length=1, description="사용자가 작성한 풀이")
    solution_plan: SolutionPlan | None = Field(default=None, description="구조화된 풀이 계획")
    video_hints: VideoHints | None = Field(default=None, description="영상 소관 시각화 힌트")
    options: VideoOptions = Field(default_factory=VideoOptions, description="렌더링 옵션")
    job_id: str | None = Field(default=None, description="영상 생성 job ID")
    user_id: str | None = Field(default=None, description="사용자 ID")
    idempotency_key: str | None = Field(default=None, description="중복 실행 방지 키")
    schema_version: int = Field(default=1, ge=1, description="계약 스키마 버전")

    @field_validator("user_solution")
    @classmethod
    def validate_user_solution(cls, value: str) -> str:
        """사용자 풀이 텍스트는 공백만으로 구성될 수 없다."""
        return _strip_non_empty(value, field_name="user_solution")


class VideoPipelineInput(VideoBaseModel):
    """영상 파이프라인 실행에 필요한 완성 입력 계약."""

    job_id: str = Field(..., min_length=1, description="영상 생성 job ID")
    problem: VideoProblem = Field(..., description="문제 입력")
    user_solution: str = Field(..., min_length=1, description="사용자가 작성한 풀이")
    solution_plan: SolutionPlan = Field(..., description="구조화된 풀이 계획")
    video_hints: VideoHints = Field(..., description="영상 시각화 힌트")
    options: VideoOptions = Field(default_factory=VideoOptions, description="렌더링 옵션")
    idempotency_key: str | None = Field(default=None, description="중복 실행 방지 키")
    schema_version: int = Field(default=1, ge=1, description="계약 스키마 버전")

    @classmethod
    def from_generation_input(cls, value: VideoGenerationInput) -> "VideoPipelineInput":
        """힌트 추출이 끝난 생성 입력을 파이프라인 입력으로 승격한다."""
        missing: list[str] = []
        if not value.job_id:
            missing.append("job_id")
        if value.solution_plan is None:
            missing.append("solution_plan")
        if value.video_hints is None:
            missing.append("video_hints")
        if missing:
            raise ValueError(f"파이프라인 입력 필드가 누락되었습니다: {', '.join(missing)}")

        return cls(
            job_id=value.job_id,
            problem=value.problem,
            user_solution=value.user_solution,
            solution_plan=value.solution_plan,
            video_hints=value.video_hints,
            options=value.options,
            idempotency_key=value.idempotency_key,
            schema_version=value.schema_version,
        )


__all__ = [
    "SolutionPlan",
    "SolutionStep",
    "VideoAspectRatio",
    "VideoErrorCode",
    "VideoErrorPayload",
    "VideoFormat",
    "VideoGenerationInput",
    "VideoHints",
    "VideoJobStatus",
    "VideoOptions",
    "VideoPipelineInput",
    "VideoProblem",
    "VideoQuality",
    "VisualizationHint",
]
