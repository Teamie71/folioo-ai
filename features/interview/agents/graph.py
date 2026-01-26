"""LangGraph 에이전트 그래프 정의"""

from langgraph.graph import END, StateGraph

from .nodes import analyst, file_processor, question_generator, retriever, supervisor
from .state import InterviewState


def build_graph():
    """
    멀티 에이전트 그래프 구성

    흐름:
    1. Supervisor가 라우팅 결정
    2. 조건에 따라 적절한 노드로 분기
    3. 각 노드는 작업 후 Supervisor로 복귀
    4. all_complete가 True가 되면 END로 종료

    Returns:
        CompiledStateGraph: 컴파일된 그래프
    """

    # StateGraph 초기화
    graph = StateGraph(InterviewState)

    # 노드 추가
    graph.add_node("supervisor", supervisor.run)
    graph.add_node("file_processor", file_processor.run)
    graph.add_node("retriever", retriever.run)
    graph.add_node("interviewer", question_generator.run)
    graph.add_node("analyst", analyst.run)

    # Supervisor -> 다른 노드들 (조건부 엣지)
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state["next_node"],
        {
            "file_processor": "file_processor",
            "retriever": "retriever",
            "interviewer": "interviewer",
            "analyst": "analyst",
            "end": END,
        },
    )

    # 모든 노드 -> Supervisor (복귀 엣지)
    graph.add_edge("file_processor", "supervisor")
    graph.add_edge("retriever", "supervisor")
    graph.add_edge("interviewer", "supervisor")
    graph.add_edge("analyst", "supervisor")

    # 진입점 설정
    graph.set_entry_point("supervisor")

    # 그래프 컴파일 - interviewer 실행 후 사용자 입력 대기
    return graph.compile(
        interrupt_after=["interviewer"]  # 질문 생성 후 중단, 사용자 입력 대기
    )


# LangGraph Studio용 그래프 인스턴스
graph = build_graph()
