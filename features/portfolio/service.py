"""포트폴리오 비즈니스 로직 서비스"""

import asyncio
import logging
from typing import TYPE_CHECKING

import asyncpg
from fastapi import BackgroundTasks

from common.db import get_pool
from features.interview import get_interview_service

from .generator import PortfolioGenerator
from .repository import PortfolioRepository
from .schemas import PortfolioOutput, PortfolioResult, PortfolioStatus

if TYPE_CHECKING:
    from app.schemas.portfolio import PortfolioStatusResponse

logger = logging.getLogger(__name__)

_service: "PortfolioService | None" = None


class PortfolioService:
    """포트폴리오 생성 오케스트레이션 서비스"""

    def __init__(
        self,
        repository: PortfolioRepository | None = None,
        generator: PortfolioGenerator | None = None,
        interview_service=None,
    ) -> None:
        self._repository = repository or PortfolioRepository(get_pool())
        self._generator = generator or PortfolioGenerator()
        self._interview_service = interview_service or get_interview_service()
        self._inprocess_tasks: set[asyncio.Task] = set()

    async def start_generation(
        self,
        session_id: str,
        user_id: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> str:
        """포트폴리오 생성을 시작하고 portfolio_id를 반환"""
        session_state = await self._interview_service.get_session_state(session_id)
        if session_state is None:
            raise ValueError(f"세션을 찾을 수 없습니다: {session_id}")

        if not session_state.get("all_stages_complete"):
            raise ValueError("인터뷰가 완료되지 않아 포트폴리오를 생성할 수 없습니다.")

        if session_state.get("user_id") != user_id:
            raise ValueError("세션 사용자와 요청 사용자가 일치하지 않습니다.")

        collected_data = session_state.get("collected_data") or {}
        experience_name = session_state.get("experience_name") or ""

        existing = await self._repository.get_by_session_id(session_id)
        if existing and existing["status"] in {
            PortfolioStatus.GENERATING.value,
            PortfolioStatus.COMPLETED.value,
        }:
            return str(existing["id"])

        if existing:
            portfolio_id = str(existing["id"])
            await self._repository.update_status(portfolio_id, PortfolioStatus.GENERATING.value)
        else:
            try:
                portfolio_id = await self._repository.create(
                    session_id=session_id,
                    user_id=user_id,
                    experience_name=experience_name,
                )
            except asyncpg.UniqueViolationError:
                # 동시 요청으로 인한 중복 생성 — 먼저 생성된 레코드 재사용
                logger.warning(
                    "session_id 중복 감지 (동시 요청), 기존 레코드 재사용: %s", session_id
                )
                row = await self._repository.get_by_session_id(session_id)
                if row is None:
                    raise RuntimeError(
                        f"session_id 충돌 후 기존 레코드를 찾을 수 없습니다: {session_id}"
                    )
                return str(row["id"])
            await self._repository.update_status(portfolio_id, PortfolioStatus.GENERATING.value)

        if background_tasks is not None:
            background_tasks.add_task(
                self._generate_portfolio_background,
                portfolio_id,
                collected_data,
                experience_name,
            )
        else:
            task = asyncio.create_task(
                self._generate_portfolio_background(portfolio_id, collected_data, experience_name)
            )
            self._inprocess_tasks.add(task)
            task.add_done_callback(self._inprocess_tasks.discard)

        return portfolio_id

    async def _generate_portfolio_background(
        self,
        portfolio_id: str,
        collected_data: dict,
        experience_name: str,
    ) -> None:
        """Background Task: 포트폴리오 생성 및 상태 업데이트"""
        try:
            output = await asyncio.to_thread(
                self._generator.generate,
                collected_data,
                experience_name,
            )
            await self._repository.update_result(portfolio_id, output)
        except Exception as exc:
            logger.exception("포트폴리오 생성 실패 (portfolio_id: %s): %s", portfolio_id, exc)
            try:
                await self._repository.update_status(
                    portfolio_id,
                    PortfolioStatus.FAILED.value,
                    str(exc),
                )
            except Exception:
                logger.exception("포트폴리오 실패 상태 업데이트 실패: %s", portfolio_id)

    async def get_status(self, portfolio_id: str) -> "PortfolioStatusResponse":
        """포트폴리오 생성 상태 조회"""
        from app.schemas.portfolio import PortfolioStatusResponse

        row = await self._repository.get_by_id(portfolio_id)
        if row is None:
            raise ValueError(f"포트폴리오를 찾을 수 없습니다: {portfolio_id}")

        status = PortfolioStatus(row["status"])
        progress_message = {
            PortfolioStatus.NOT_STARTED: "포트폴리오 생성을 준비하고 있습니다.",
            PortfolioStatus.GENERATING: "포트폴리오를 생성하고 있습니다.",
            PortfolioStatus.COMPLETED: "포트폴리오 생성이 완료되었습니다.",
            PortfolioStatus.FAILED: row.get("error_message")
            or "포트폴리오 생성에 실패했습니다. 잠시 후 다시 시도하거나 관리자에게 문의해주세요.",
        }[status]

        return PortfolioStatusResponse(status=status, progress_message=progress_message)

    async def get_result(self, portfolio_id: str) -> PortfolioResult | None:
        """완료된 포트폴리오 결과 조회"""
        row = await self._repository.get_by_id(portfolio_id)
        if row is None or row["status"] != PortfolioStatus.COMPLETED.value:
            return None
        return self._to_result(row)

    async def get_by_session(self, session_id: str) -> PortfolioResult | None:
        """세션 ID로 완료된 포트폴리오 결과 조회"""
        row = await self._repository.get_by_session_id(session_id)
        if row is None or row["status"] != PortfolioStatus.COMPLETED.value:
            return None
        return self._to_result(row)

    async def has_generating_or_completed(self, session_id: str) -> bool:
        """세션에 생성 중/완료 상태의 포트폴리오가 있는지 확인"""
        row = await self._repository.get_by_session_id(session_id)
        if row is None:
            return False
        return row["status"] in {PortfolioStatus.GENERATING.value, PortfolioStatus.COMPLETED.value}

    async def exists(self, portfolio_id: str) -> bool:
        """포트폴리오 존재 여부 확인"""
        row = await self._repository.get_by_id(portfolio_id)
        return row is not None

    async def update_contribution_rate(self, portfolio_id: str, rate: int) -> None:
        """포트폴리오 기여도 수정"""
        if not 0 <= rate <= 100:
            raise ValueError("기여도는 0에서 100 사이여야 합니다.")
        await self._repository.update_contribution_rate(portfolio_id, rate)

    @staticmethod
    def _to_result(row: dict) -> PortfolioResult:
        output: PortfolioOutput | None = None
        if all(
            row.get(key) is not None
            for key in ["description", "contributions", "achievements", "insights"]
        ):
            output = PortfolioOutput(
                description=row["description"],
                contributions=row["contributions"],
                achievements=row["achievements"],
                insights=row["insights"],
            )

        return PortfolioResult(
            portfolio_id=str(row["id"]),
            session_id=row["session_id"],
            user_id=row["user_id"],
            experience_name=row["experience_name"],
            status=PortfolioStatus(row["status"]),
            contribution_rate=row.get("contribution_rate"),
            output=output,
            created_at=row["created_at"],
        )


def get_portfolio_service() -> PortfolioService:
    """PortfolioService 싱글톤 반환"""
    global _service

    if _service is None:
        _service = PortfolioService()

    return _service


def reset_portfolio_service() -> None:
    """PortfolioService 싱글톤 초기화 (테스트용)"""
    global _service
    _service = None
