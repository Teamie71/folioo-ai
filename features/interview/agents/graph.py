"""LangGraph 에이전트 그래프 정의"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from .nodes import analyst, file_processor, question_generator, retriever, router
from .state import InterviewState


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """
    멀티 에이전트 그래프 구성 (단일 멀티턴 구조)

    Args:
        checkpointer: 상태 영속화를 위한 checkpointer (선택)
                      None이면 stateless로 동작 (LangGraph Studio 호환)

    흐름:
    [첫 턴]
    Router (초기 감지) → QuestionGenerator → END
    [이후 턴 - 파일 있음]
    Router → FileProcessor → Retriever → Analyst → QuestionGenerator → END
    [이후 턴 - 파일 없음]
    Router → Retriever → Analyst → QuestionGenerator → END

    Returns:
        CompiledStateGraph: 컴파일된 그래프
    """

    # StateGraph 초기화
    graph = StateGraph(InterviewState)

    # 노드 추가
    graph.add_node("router", router.run)
    graph.add_node("file_processor", file_processor.run)
    graph.add_node("retriever", retriever.run)
    graph.add_node("question_generator", question_generator.run)
    graph.add_node("analyst", analyst.run)

    # Router -> 조건부 분기
    graph.add_conditional_edges(
        "router",
        lambda state: state["next_node"],
        {
            "question_generator": "question_generator",  # 첫 턴
            "file_processor": "file_processor",  # 파일 있음
            "retriever": "retriever",  # 파일 없음
        },
    )

    # FileProcessor -> Retriever -> Analyst
    graph.add_edge("file_processor", "retriever")
    graph.add_edge("retriever", "analyst")
    # Analyst -> 조건부 분기 (단계 진행 또는 인터뷰 종료)
    graph.add_conditional_edges(
        "analyst",
        lambda state: state["next_node"],
        {
            "question_generator": "question_generator",
            "end": END,
        },
    )
    # QuestionGenerator -> END
    graph.add_edge("question_generator", END)

    # 진입점 설정
    graph.set_entry_point("router")

    # 그래프 컴파일 (checkpointer 연결)
    return graph.compile(checkpointer=checkpointer)


# LangGraph Studio용 그래프 인스턴스 (checkpointer 없이)
graph = build_graph()
