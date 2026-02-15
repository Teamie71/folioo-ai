"""pgvector 기반 인사이트 저장소 구현체 - 임시 PostgreSQL 직접 연결"""

import logging
import os

import asyncpg
from openai import AsyncOpenAI

from ..state import InsightLog

logger = logging.getLogger(__name__)

# 임베딩 설정
_EMBEDDING_MODEL = "openai/text-embedding-3-small"
_EMBEDDING_DIMENSIONS = 512


def _create_embedding_client() -> AsyncOpenAI:
    """
    OpenRouter 임베딩 API용 AsyncOpenAI 클라이언트 생성
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    if not api_key:
        raise ValueError("OPENROUTER_API_KEY 환경 변수가 설정되지 않았습니다.")

    return AsyncOpenAI(api_key=api_key, base_url=base_url)


class PgVectorInsightStore:
    """
    pgvector 기반 인사이트 저장소

    - 임시 PostgreSQL DB에 직접 연결하여 벡터 검색 수행
    - OpenRouter를 통한 OpenAI 임베딩 모델로 query → 벡터 변환
    - pgvector의 cosine distance 연산자로 유사도 검색
    - 메인 서버 API 연동 전 로컬 테스트용

    사용법:
        store = PgVectorInsightStore(pool=pool)
        results = await store.search_similar("검색어", user_id="user-1")
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._openai_client = _create_embedding_client()

    async def _get_embedding(self, text: str) -> list[float]:
        """텍스트를 임베딩 벡터로 변환 (OpenRouter → OpenAI text-embedding-3-small)"""
        response = await self._openai_client.embeddings.create(
            model=_EMBEDDING_MODEL,
            input=text,
            dimensions=_EMBEDDING_DIMENSIONS,
        )
        return response.data[0].embedding

    async def setup_table(self) -> None:
        """
        pgvector extension 활성화 및 인사이트 테이블 생성

        - 이미 존재하는 경우 무시됩니다.
        - 서버 시작 시(lifespan) 1회 호출합니다.
        """
        async with self._pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS insight_logs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    activity_name TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding vector({_EMBEDDING_DIMENSIONS}),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            # user_id 인덱스
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_insight_logs_user_id
                ON insight_logs (user_id);
            """)
            logger.info("insight_logs 테이블 준비 완료")

    async def add_insight(self, insight: InsightLog, user_id: str) -> None:
        """
        인사이트를 저장소에 추가하고 임베딩을 생성합니다.

        Args:
            insight: 인사이트 로그 데이터
            user_id: 소유자 사용자 ID
        """
        text_for_embedding = f"{insight['title']}\n{insight['content']}"

        try:
            embedding = await self._get_embedding(text_for_embedding)
        except Exception:
            logger.exception(f"임베딩 생성 실패: insight_id={insight['id']}")
            embedding = None
        embedding_str = f"[{','.join(str(v) for v in embedding)}]" if embedding else None

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO insight_logs (id, user_id, title, activity_name, category, content, embedding)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding
                """,
                insight["id"],
                user_id,
                insight["title"],
                insight.get("activity_name", ""),
                insight["category"],
                insight["content"],
                embedding_str,
            )
        logger.info(f"인사이트 저장됨: {insight['id']} (user={user_id})")

    async def search_similar(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        threshold: float = 0.7,
    ) -> list[InsightLog]:
        """
        pgvector 코사인 유사도 기반 인사이트 검색
        Args:
            query: 검색 텍스트
            user_id: 사용자 ID
            top_k: 최대 반환 결과 수
            threshold: 코사인 유사도 임계값
        Returns:
            유사 인사이트 목록 (유사도 내림차순, threshold 이상만)
        """
        try:
            query_embedding = await self._get_embedding(query)
        except Exception:
            logger.exception("검색어 임베딩 생성 실패")
            return []
        embedding_str = f"[{','.join(str(v) for v in query_embedding)}]"
        # pgvector: cosine distance = 1 - cosine_similarity
        # 따라서 similarity = 1 - distance
        # threshold 0.7 → distance < 0.3
        max_distance = 1.0 - threshold
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title, activity_name, category, content,
                       1 - (embedding <=> $1::vector) AS similarity_score
                FROM insight_logs
                WHERE user_id = $2
                  AND embedding IS NOT NULL
                  AND (embedding <=> $1::vector) < $3
                ORDER BY embedding <=> $1::vector
                LIMIT $4
                """,
                embedding_str,
                user_id,
                max_distance,
                top_k,
            )
        results: list[InsightLog] = [
            {
                "id": row["id"],
                "title": row["title"],
                "activity_name": row["activity_name"],
                "category": row["category"],
                "content": row["content"],
                "similarity_score": round(float(row["similarity_score"]), 4),
            }
            for row in rows
        ]

        # 검색 결과 요약 로그
        query_preview = query[:50].replace("\n", " ")
        logger.info(
            "🔍 유사 인사이트 검색 완료: query='%s%s', user=%s, threshold=%.2f, top_k=%d, found=%d",
            query_preview,
            "..." if len(query) > 50 else "",
            user_id,
            threshold,
            top_k,
            len(results),
        )

        # 각 결과의 유사도 수치 상세 로그
        if results:
            for i, r in enumerate(results, 1):
                logger.info(
                    "  📊 [%d] score=%.4f | id=%s | title='%s'",
                    i,
                    r["similarity_score"],
                    r["id"],
                    r["title"][:40],
                )
        else:
            logger.info("  ⚠️ 임계값(%.2f) 이상의 유사 인사이트 없음", threshold)

        return results

    async def get_by_id(self, insight_id: str) -> InsightLog | None:
        """
        인사이트 단건 조회

        Args:
            insight_id: 인사이트 로그 ID

        Returns:
            인사이트 로그 데이터, 없으면 None
        """

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, title, activity_name, category, content "
                "FROM insight_logs WHERE id = $1",
                insight_id,
            )

        if row is None:
            return None

        return {
            "id": row["id"],
            "title": row["title"],
            "activity_name": row["activity_name"],
            "category": row["category"],
            "content": row["content"],
            "similarity_score": None,
        }

        async def clear(self) -> None:
            """저장소 초기화 (테스트용)"""

            async with self._pool.acquire() as conn:
                await conn.execute("DELETE FROM insight_logs")

        async def count(self) -> int:
            """저장된 인사이트 수"""

            async with self._pool.acquire() as conn:
                return await conn.fetchval("SELECT COUNT(*) FROM insight_logs")
