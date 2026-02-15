"""인사이트 저장소 프로토콜 정의"""

from typing import Protocol, runtime_checkable

from ..state import InsightLog


@runtime_checkable
class InsightStore(Protocol):
    """
    인사이트 로그 저장소 인터페이스

    Retriever 노드가 이 프로토콜에 의존하므로,
    구현체를 교체하면 데이터 소스를 변경할 수 있습니다.

    구현체:
    - PgVectorInsightStore: 임시 pgvector 기반
    - (향후) MainServerInsightStore: 메인 서버 API 연동
    """

    async def search_similar(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        threshold: float = 0.7,
    ) -> list[InsightLog]:
        """
        텍스트 기반 인사이트 로그 검색

        Args:
            query: 검색 텍스트 (이전 대화 + 현재 턴 사용자 답변)
            user_id: 사용자 ID (해당 사용자의 인사이트만 검색)
            top_k: 최대 반환 결과 수
            threshold: 코사인 유사도 임계값

        Returns:
            유사 인사이트 목록 (유사도 내림차순)
        """
        ...

    async def get_by_id(self, insight_id: str) -> InsightLog | None:
        """
        인사이트 단건 조회

        Args:
            insight_id: 인사이트 로그 ID

        Returns:
            인사이트 로그 데이터, 없으면 None
        """
        ...
