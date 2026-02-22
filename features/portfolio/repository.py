"""포트폴리오 DB Repository (asyncpg 직접 쿼리)"""

import logging

import asyncpg

from .schemas import PortfolioOutput

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS portfolios (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        VARCHAR     NOT NULL UNIQUE,
    user_id           VARCHAR     NOT NULL,
    experience_name   VARCHAR     NOT NULL,
    status            VARCHAR     NOT NULL DEFAULT 'not_started',
    detail_info       TEXT,
    assigned_task     TEXT,
    problem_solving   TEXT,
    lessons_learned   TEXT,
    contribution_rate INTEGER     DEFAULT 0,
    error_message     TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);
"""


class PortfolioRepository:
    """
    포트폴리오 DB 접근 Repository

    asyncpg 커넥션 풀을 의존성으로 주입받아 Raw SQL로 portfolios 테이블을 조작합니다.
    추후 httpx(메인 백엔드 API 호출) 방식으로 교체 시 이 클래스 내부만 변경하면 됩니다.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def setup_table(self) -> None:
        """portfolios 테이블이 없으면 생성"""
        await self._pool.execute(_CREATE_TABLE_SQL)
        logger.info("portfolios 테이블 준비 완료")

    async def create(
        self,
        session_id: str,
        user_id: str,
        experience_name: str,
    ) -> str:
        """
        포트폴리오 레코드 생성

        Args:
            session_id: 인터뷰 세션 ID (UNIQUE)
            user_id: 사용자 ID
            experience_name: 경험/프로젝트명

        Returns:
            생성된 portfolio_id (UUID 문자열)

        Raises:
            asyncpg.UniqueViolationError: 동일 session_id가 이미 존재하는 경우
        """
        row = await self._pool.fetchrow(
            """
            INSERT INTO portfolios (session_id, user_id, experience_name, status)
            VALUES ($1, $2, $3, 'not_started')
            RETURNING id::text
            """,
            session_id,
            user_id,
            experience_name,
        )
        return row["id"]

    async def get_by_id(self, portfolio_id: str) -> dict | None:
        """
        ID로 포트폴리오 조회

        Args:
            portfolio_id: 포트폴리오 UUID 문자열

        Returns:
            포트폴리오 row dict 또는 None
        """
        row = await self._pool.fetchrow(
            "SELECT * FROM portfolios WHERE id = $1::uuid",
            portfolio_id,
        )
        return dict(row) if row else None

    async def get_by_session_id(self, session_id: str) -> dict | None:
        """
        세션 ID로 포트폴리오 조회

        Args:
            session_id: 인터뷰 세션 ID

        Returns:
            포트폴리오 row dict 또는 None
        """
        row = await self._pool.fetchrow(
            "SELECT * FROM portfolios WHERE session_id = $1",
            session_id,
        )
        return dict(row) if row else None

    async def update_result(
        self,
        portfolio_id: str,
        output: PortfolioOutput,
    ) -> None:
        """
        생성 결과 저장 + status를 completed로 변경

        Args:
            portfolio_id: 포트폴리오 ID
            output: LLM이 생성한 포트폴리오 내용
        """
        await self._pool.execute(
            """
            UPDATE portfolios
            SET detail_info     = $2,
                assigned_task   = $3,
                problem_solving = $4,
                lessons_learned = $5,
                status          = 'completed',
                error_message   = NULL,
                updated_at      = NOW()
            WHERE id = $1::uuid
            """,
            portfolio_id,
            output.detail_info,
            output.assigned_task,
            output.problem_solving,
            output.lessons_learned,
        )

    async def update_status(
        self,
        portfolio_id: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """
        상태 변경

        Args:
            portfolio_id: 포트폴리오 ID
            status: 변경할 상태 (not_started / generating / completed / failed)
            error_message: 실패 시 에러 메시지 (optional)
        """
        await self._pool.execute(
            """
            UPDATE portfolios
            SET status        = $2,
                error_message = $3,
                updated_at    = NOW()
            WHERE id = $1::uuid
            """,
            portfolio_id,
            status,
            error_message,
        )

    async def update_contribution_rate(
        self,
        portfolio_id: str,
        rate: int,
    ) -> None:
        """
        기여도 수정

        Args:
            portfolio_id: 포트폴리오 ID
            rate: 기여도 (0-100)
        """
        await self._pool.execute(
            """
            UPDATE portfolios
            SET contribution_rate = $2,
                updated_at        = NOW()
            WHERE id = $1::uuid
            """,
            portfolio_id,
            rate,
        )
