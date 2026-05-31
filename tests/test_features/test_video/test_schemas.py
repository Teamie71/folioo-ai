"""영상 생성 데이터 계약 테스트."""

import pytest
from pydantic import ValidationError

from features.video import (
    SolutionPlan,
    VideoErrorCode,
    VideoGenerationInput,
    VideoHints,
    VideoOptions,
    VideoPipelineInput,
    VideoProblem,
)
from features.video.exceptions import VideoRenderTimeoutError


def test_video_generation_input_validates_and_serializes_with_aliases() -> None:
    """입력/계획/힌트/옵션 계약을 import, 검증, 직렬화할 수 있다."""
    value = VideoGenerationInput.model_validate(
        {
            "jobId": "job-1",
            "userId": "user-1",
            "idempotencyKey": "idem-1",
            "schemaVersion": 1,
            "problem": {
                "problemId": "problem-1",
                "statement": "2x + 3 = 7을 풀어라.",
                "subject": "math",
            },
            "userSolution": "양변에서 3을 빼고 2로 나누면 x=2입니다.",
            "solutionPlan": _solution_plan_payload(),
            "videoHints": _video_hints_payload(),
            "options": {
                "format": "mp4",
                "quality": "standard",
                "aspectRatio": "16:9",
                "resolutionWidth": 1280,
                "resolutionHeight": 720,
                "fps": 30,
                "maxDurationSeconds": 120,
                "targetDurationSeconds": 60,
            },
        }
    )

    assert value.problem.problem_id == "problem-1"
    assert value.solution_plan is not None
    assert value.video_hints is not None
    assert value.solution_plan.steps[0].order == 1
    assert value.video_hints.visualization_hints[0].step_order == 1

    dumped = value.model_dump(mode="json", by_alias=True)
    assert dumped["jobId"] == "job-1"
    assert dumped["userSolution"] == "양변에서 3을 빼고 2로 나누면 x=2입니다."
    assert dumped["solutionPlan"]["steps"][0]["keyPoints"] == ["역연산"]
    assert dumped["videoHints"]["visualizationHints"][0]["kind"] == "equation"
    assert dumped["options"]["targetDurationSeconds"] == 60


def test_solution_plan_rejects_visualization_hints_field() -> None:
    """visualization_hints 는 SolutionPlan 이 아니라 VideoHints 가 소유한다."""
    payload = _solution_plan_payload()
    payload["visualization_hints"] = []

    with pytest.raises(ValidationError, match="Extra inputs"):
        SolutionPlan.model_validate(payload)


def test_solution_plan_requires_contiguous_step_order() -> None:
    """풀이 단계 순서는 힌트 매핑을 위해 1부터 연속되어야 한다."""
    payload = _solution_plan_payload()
    payload["steps"][1]["order"] = 3

    with pytest.raises(ValidationError, match="steps.order"):
        SolutionPlan.model_validate(payload)


def test_video_hints_reject_duplicate_hint_ids() -> None:
    """중복 hint_id 는 파이프라인 stage 추적을 모호하게 하므로 거부한다."""
    payload = _video_hints_payload()
    payload["visualizationHints"].append(
        {
            "hintId": "hint-1",
            "stepOrder": 2,
            "kind": "highlight",
            "description": "최종 답을 강조한다.",
        }
    )

    with pytest.raises(ValidationError, match="hint_id 중복"):
        VideoHints.model_validate(payload)


def test_video_options_validate_duration_budget() -> None:
    """목표 길이는 최대 길이 예산을 넘을 수 없다."""
    with pytest.raises(ValidationError, match="target_duration_seconds"):
        VideoOptions(max_duration_seconds=30, target_duration_seconds=60)


def test_pipeline_input_requires_completed_contract() -> None:
    """파이프라인 입력 승격은 job_id, solution_plan, video_hints 를 요구한다."""
    generation_input = VideoGenerationInput(
        job_id="job-1",
        problem=VideoProblem(statement="문제"),
        user_solution="풀이",
        solution_plan=SolutionPlan.model_validate(_solution_plan_payload()),
        video_hints=VideoHints.model_validate(_video_hints_payload()),
    )

    pipeline_input = VideoPipelineInput.from_generation_input(generation_input)

    assert pipeline_input.job_id == "job-1"
    assert pipeline_input.solution_plan.final_answer == "x = 2"
    assert pipeline_input.video_hints.visualization_hints[0].hint_id == "hint-1"


def test_pipeline_input_reports_missing_contract_fields() -> None:
    """힌트 추출 전 입력은 파이프라인 입력으로 사용할 수 없다."""
    generation_input = VideoGenerationInput(
        problem=VideoProblem(statement="문제"),
        user_solution="풀이",
    )

    with pytest.raises(ValueError, match="job_id, solution_plan, video_hints"):
        VideoPipelineInput.from_generation_input(generation_input)


def test_video_exception_payload_does_not_expose_internal_detail() -> None:
    """예외 payload 는 원시 stderr/traceback 대신 안전한 코드와 메시지만 노출한다."""
    exc = VideoRenderTimeoutError(
        internal_detail="stderr: ffmpeg SECRET traceback",
        details={"stage": "render"},
    )

    payload = exc.to_error_payload()
    dumped = payload.model_dump(mode="json", by_alias=True)

    assert exc.error_code == VideoErrorCode.RENDER_TIMEOUT
    assert exc.retryable is True
    assert payload.error_code == VideoErrorCode.RENDER_TIMEOUT
    assert payload.retryable is True
    assert dumped == {
        "errorCode": "RENDER_TIMEOUT",
        "message": "영상 렌더링 시간이 초과되었습니다.",
        "retryable": True,
        "details": {"stage": "render"},
    }
    assert "SECRET" not in dumped["message"]


def _solution_plan_payload() -> dict:
    return {
        "summary": "일차방정식의 양변에 같은 연산을 적용한다.",
        "finalAnswer": "x = 2",
        "concepts": ["일차방정식", "역연산"],
        "steps": [
            {
                "order": 1,
                "title": "상수항 제거",
                "explanation": "양변에서 3을 빼서 2x=4를 만든다.",
                "equation": "2x + 3 - 3 = 7 - 3",
                "keyPoints": ["역연산"],
            },
            {
                "order": 2,
                "title": "계수로 나누기",
                "explanation": "양변을 2로 나누어 x=2를 얻는다.",
                "equation": "2x / 2 = 4 / 2",
            },
        ],
    }


def _video_hints_payload() -> dict:
    return {
        "visualizationHints": [
            {
                "hintId": "hint-1",
                "stepOrder": 1,
                "kind": "equation",
                "description": "양변에서 3이 사라지는 과정을 애니메이션으로 보여준다.",
                "target": "2x + 3 = 7",
                "priority": "high",
                "durationSeconds": 5,
            }
        ],
        "narrationTone": "friendly",
        "narrationLanguage": "ko-KR",
        "styleKeywords": ["clean", "math"],
        "emphasisTerms": ["양변", "역연산"],
    }
