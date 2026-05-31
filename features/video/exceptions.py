"""영상 생성 예외 계열."""

from typing import ClassVar

from features.video.schemas import VideoErrorCode, VideoErrorPayload

_SAFE_ERROR_MESSAGES: dict[VideoErrorCode, str] = {
    VideoErrorCode.INVALID_INPUT: "영상 생성 요청을 확인할 수 없습니다.",
    VideoErrorCode.SOLUTION_PLAN_INVALID: "풀이 계획을 영상으로 변환할 수 없습니다.",
    VideoErrorCode.HINT_EXTRACTION_FAILED: "영상 힌트를 추출하지 못했습니다.",
    VideoErrorCode.UNSUPPORTED_OPTION: "지원하지 않는 영상 옵션입니다.",
    VideoErrorCode.RENDER_TIMEOUT: "영상 렌더링 시간이 초과되었습니다.",
    VideoErrorCode.RENDER_FAILED: "영상 렌더링에 실패했습니다.",
    VideoErrorCode.STORAGE_FAILED: "영상 파일 저장에 실패했습니다.",
    VideoErrorCode.PIPELINE_FAILED: "영상 생성 중 오류가 발생했습니다.",
    VideoErrorCode.JOB_NOT_FOUND: "영상 생성 작업을 찾을 수 없습니다.",
}


class VideoGenerationError(Exception):
    """영상 생성 도메인의 기본 예외."""

    default_error_code: ClassVar[VideoErrorCode] = VideoErrorCode.PIPELINE_FAILED
    default_retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str | None = None,
        *,
        error_code: VideoErrorCode | str | None = None,
        retryable: bool | None = None,
        internal_detail: str | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        code = VideoErrorCode(error_code) if error_code is not None else self.default_error_code
        self.error_code = code
        self.retryable = self.default_retryable if retryable is None else retryable
        self.safe_message = message or _SAFE_ERROR_MESSAGES[code]
        self.internal_detail = internal_detail
        self.details = details
        super().__init__(self.safe_message)

    def to_error_payload(self) -> VideoErrorPayload:
        """원시 stderr/traceback 을 제외한 안전한 에러 payload 를 반환한다."""
        return VideoErrorPayload(
            error_code=self.error_code,
            message=self.safe_message,
            retryable=self.retryable,
            details=self.details,
        )


class VideoContractError(VideoGenerationError):
    """입력 계약 위반 또는 지원하지 않는 옵션."""

    default_error_code = VideoErrorCode.INVALID_INPUT


class VideoSolutionPlanError(VideoGenerationError):
    """풀이 계획 검증/변환 실패."""

    default_error_code = VideoErrorCode.SOLUTION_PLAN_INVALID


class VideoUnsupportedOptionError(VideoGenerationError):
    """지원하지 않는 영상 옵션."""

    default_error_code = VideoErrorCode.UNSUPPORTED_OPTION


class VideoHintExtractionError(VideoGenerationError):
    """힌트 추출 실패."""

    default_error_code = VideoErrorCode.HINT_EXTRACTION_FAILED
    default_retryable = True


class VideoRenderTimeoutError(VideoGenerationError):
    """영상 렌더링 타임아웃."""

    default_error_code = VideoErrorCode.RENDER_TIMEOUT
    default_retryable = True


class VideoRenderError(VideoGenerationError):
    """영상 렌더링 실패."""

    default_error_code = VideoErrorCode.RENDER_FAILED


class VideoStorageError(VideoGenerationError):
    """영상 산출물 저장 실패."""

    default_error_code = VideoErrorCode.STORAGE_FAILED
    default_retryable = True


class VideoPipelineError(VideoGenerationError):
    """영상 파이프라인 일반 실패."""

    default_error_code = VideoErrorCode.PIPELINE_FAILED


class VideoJobNotFoundError(VideoGenerationError):
    """영상 job 을 찾을 수 없음."""

    default_error_code = VideoErrorCode.JOB_NOT_FOUND


__all__ = [
    "VideoContractError",
    "VideoGenerationError",
    "VideoHintExtractionError",
    "VideoJobNotFoundError",
    "VideoPipelineError",
    "VideoRenderError",
    "VideoRenderTimeoutError",
    "VideoSolutionPlanError",
    "VideoStorageError",
    "VideoUnsupportedOptionError",
]
