"""첨삭 DB Repository (asyncpg 직접 쿼리)"""

import json
import logging

import asyncpg

logger = logging.getLogger(__name__)

_repo: "CorrectionRepository | None" = None

_CREATE_CORRECTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    portfolio_id VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    company_name VARCHAR NOT NULL,
    job_title VARCHAR NOT NULL,
    job_description TEXT NOT NULL,
    emphasis_points TEXT,
    company_insight TEXT,
    status VARCHAR NOT NULL DEFAULT 'not_started',
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CREATE_RAG_DATA_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS rag_data (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    correction_id UUID NOT NULL REFERENCES corrections(id) ON DELETE CASCADE,
    search_query VARCHAR NOT NULL,
    search_results JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _loads_json_if_string(value: object) -> object:
    """문자열 JSON 값을 파이썬 객체로 변환"""
    if not isinstance(value, str):
        return value

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


class CorrectionRepository:
    """
    첨삭 DB 접근 Repository

    asyncpg 커넥션 풀을 의존성으로 주입받아 Raw SQL로
    corrections, rag_data 테이블을 조작합니다.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def setup_table(self) -> None:
        """corrections, rag_data 테이블이 없으면 생성"""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(_CREATE_CORRECTIONS_TABLE_SQL)
                await conn.execute(_CREATE_RAG_DATA_TABLE_SQL)

        logger.info("corrections, rag_data 테이블 준비 완료")

    async def create(
        self,
        portfolio_id: str,
        user_id: str,
        company_name: str,
        job_title: str,
        job_description: str,
    ) -> dict:
        """
        첨삭 레코드 생성

        Args:
            portfolio_id: 포트폴리오 ID
            user_id: 사용자 ID
            company_name: 회사명
            job_title: 직무명
            job_description: JD 원문

        Returns:
            생성된 corrections row dict
        """
        row = await self._pool.fetchrow(
            """
            INSERT INTO corrections (
                portfolio_id,
                user_id,
                company_name,
                job_title,
                job_description,
                status
            )
            VALUES ($1, $2, $3, $4, $5, 'not_started')
            RETURNING *
            """,
            portfolio_id,
            user_id,
            company_name,
            job_title,
            job_description,
        )
        if row is None:
            raise RuntimeError("첨삭 레코드 생성에 실패했습니다.")
        return self._to_correction_row(row)

    async def get_by_id(self, correction_id: str) -> dict | None:
        """
        ID로 첨삭 조회

        Args:
            correction_id: 첨삭 UUID 문자열

        Returns:
            첨삭 row dict 또는 None
        """
        row = await self._pool.fetchrow(
            "SELECT * FROM corrections WHERE id = $1::uuid",
            correction_id,
        )
        return self._to_correction_row(row)

    async def update_status(self, correction_id: str, status: str) -> None:
        """
        첨삭 상태 변경

        Args:
            correction_id: 첨삭 ID
            status: 변경할 상태
        """
        await self._pool.execute(
            """
            UPDATE corrections
            SET status = $2,
                updated_at = NOW()
            WHERE id = $1::uuid
            """,
            correction_id,
            status,
        )

    async def update_result(self, correction_id: str, result: dict) -> None:
        """
        첨삭 결과 저장 + 상태 done으로 변경

        Args:
            correction_id: 첨삭 ID
            result: 첨삭 결과(JSON 직렬화 가능한 dict)
        """
        await self._pool.execute(
            """
            UPDATE corrections
            SET result = $2::jsonb,
                status = 'done',
                updated_at = NOW()
            WHERE id = $1::uuid
            """,
            correction_id,
            json.dumps(result, ensure_ascii=False),
        )

    async def update_company_insight(self, correction_id: str, company_insight: str) -> None:
        """
        기업 인사이트 내용 업데이트

        Args:
            correction_id: 첨삭 ID
            company_insight: 기업 분석/인사이트 텍스트
        """
        await self._pool.execute(
            """
            UPDATE corrections
            SET company_insight = $2,
                updated_at = NOW()
            WHERE id = $1::uuid
            """,
            correction_id,
            company_insight,
        )

    async def update_emphasis_points(self, correction_id: str, emphasis_points: str) -> None:
        """
        강조 포인트 업데이트

        Args:
            correction_id: 첨삭 ID
            emphasis_points: 강조 포인트 텍스트
        """
        await self._pool.execute(
            """
            UPDATE corrections
            SET emphasis_points = $2,
                updated_at = NOW()
            WHERE id = $1::uuid
            """,
            correction_id,
            emphasis_points,
        )

    async def delete(self, correction_id: str) -> None:
        """
        첨삭 레코드 삭제

        Args:
            correction_id: 첨삭 ID
        """
        await self._pool.execute(
            "DELETE FROM corrections WHERE id = $1::uuid",
            correction_id,
        )

    async def save_rag_data(
        self,
        correction_id: str,
        search_query: str,
        search_results: dict,
    ) -> None:
        """
        RAG 검색 결과 저장

        Args:
            correction_id: 첨삭 ID
            search_query: 검색어
            search_results: 검색 결과(JSON 직렬화 가능한 dict)
        """
        await self._pool.execute(
            """
            INSERT INTO rag_data (correction_id, search_query, search_results)
            VALUES ($1::uuid, $2, $3::jsonb)
            """,
            correction_id,
            search_query,
            json.dumps(search_results, ensure_ascii=False),
        )

    async def get_rag_data(self, correction_id: str) -> list[dict]:
        """
        첨삭 ID 기준 RAG 검색 결과 목록 조회

        Args:
            correction_id: 첨삭 ID

        Returns:
            rag_data row dict 리스트 (created_at 오름차순)
        """
        rows = await self._pool.fetch(
            """
            SELECT *
            FROM rag_data
            WHERE correction_id = $1::uuid
            ORDER BY created_at ASC
            """,
            correction_id,
        )

        return [self._to_rag_data_row(row) for row in rows]

    @staticmethod
    def _to_correction_row(row: asyncpg.Record | None) -> dict | None:
        """corrections Record를 dict로 변환"""
        if row is None:
            return None

        data = dict(row)
        data["result"] = _loads_json_if_string(data.get("result"))
        return data

    @staticmethod
    def _to_rag_data_row(row: asyncpg.Record) -> dict:
        """rag_data Record를 dict로 변환"""
        data = dict(row)
        data["search_results"] = _loads_json_if_string(data.get("search_results"))
        return data


def get_correction_repository() -> CorrectionRepository:
    """CorrectionRepository 싱글톤 반환"""
    if _repo is None:
        raise RuntimeError(
            "CorrectionRepository가 초기화되지 않았습니다. "
            "init_correction_repository()를 먼저 호출하세요."
        )
    return _repo


def init_correction_repository(pool: asyncpg.Pool) -> CorrectionRepository:
    """CorrectionRepository 싱글톤 초기화"""
    global _repo
    _repo = CorrectionRepository(pool)
    return _repo


def reset_correction_repository() -> None:
    """CorrectionRepository 싱글톤 초기화 (테스트용)"""
    global _repo
    _repo = None


__all__ = [
    "CorrectionRepository",
    "get_correction_repository",
    "init_correction_repository",
    "reset_correction_repository",
]
