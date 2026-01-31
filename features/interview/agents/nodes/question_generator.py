"""QuestionGenerator 노드 - 인터뷰 질문 생성"""
from langchain_core.messages import AIMessage
from common.llm.client import get_llm
from features.interview.config.loader import load_stage_config

from ..prompts.question_generator import first_turn_prompt
from ..state import InterviewState


def run(state: InterviewState) -> InterviewState:
    """
    인터뷰 질문 생성 (초기 또는 분석 기반)
    - 첫 턴: 첫 질문 생성
    - 이후 턴: Analyst 분석 결과 기반 질문 생성
    """

    # 1. 현재 단계 설정 로드
    stage_config = load_stage_config(state["current_stage"])
    progress = state["stage_progress"]
    
    # 2. 첫 턴 판단
    is_first_turn = len(state["messages"])==0
    if is_first_turn:
        # 3. 첫 고정 질문 내용 가져오기
        if not stage_config.fixed_questions:
            raise ValueError(f"Stage {state['current_stage']}에 고정 질문이 설정되지 않았습니다.")
        fixed_question_raw = stage_config.fixed_questions[0]
        
        # 4. 플레이스홀더 치환
        fixed_question_content = fixed_question_raw.replace(
            "[경험명]", state["experience_name"]
        )
        
        # 5. LLM으로 자연스러운 질문 생성
        llm = get_llm(temperature=0.7)
        # LangChain LCEL 체인 사용
        chain = first_turn_prompt | llm
        
        try:
            response = chain.invoke({
                "experience_name": state["experience_name"],
                "fixed_question_content": fixed_question_content
            })
            question = response.content
        except Exception as e:
            # LLM 호출 실패 시 고정 질문을 fallback으로 사용
            print(f"LLM 호출 실패: {e}")
            question = fixed_question_content
            # 상태에 에러 기록 (선택적)
            state = {**state, "llm_error": str(e)}
        
        # 6. 진행 상황 업데이트
        updated_progress = {
            **progress,
            "fixed_q_used": 1,
        }
        
        # 7. AI 메시지 추가
        return {
            **state,
            "messages": [AIMessage(content=question)],
            "stage_progress": updated_progress,
            "next_node": "end"
        }
    else:
        # TODO: 후속 질문 생성 로직 (다른 이슈에서 구현)
        raise NotImplementedError("후속 질문 생성은 아직 구현되지 않았습니다.")
