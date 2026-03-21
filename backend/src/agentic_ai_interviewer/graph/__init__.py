from langgraph.graph import StateGraph, END
from agentic_ai_interviewer.state import InterviewState
from agentic_ai_interviewer.nodes import (
    extract_jd_skills, orchestrator_service, orchestrator_web_search,
    question_generator, check_db_indexes, refine_web_search, 
    refine_question, answer_evaluator, generate_report
)

def build_graph():
    workflow = StateGraph(InterviewState)
    
    # 1. Add nodes
    workflow.add_node("extract_jd_skills", extract_jd_skills)
    workflow.add_node("orchestrator_service", orchestrator_service)
    workflow.add_node("orchestrator_web_search", orchestrator_web_search)
    workflow.add_node("question_generator", question_generator)
    workflow.add_node("check_db_indexes", check_db_indexes)
    workflow.add_node("refine_web_search", refine_web_search)
    workflow.add_node("refine_question", refine_question)
    workflow.add_node("answer_evaluator", answer_evaluator)
    workflow.add_node("generate_report", generate_report)
    
    # 2. Add edges and conditional routing
    workflow.set_entry_point("extract_jd_skills")
    workflow.add_edge("extract_jd_skills", "orchestrator_service")
    
    def orchestrator_condition(state: InterviewState):
        return "search" if state.get("orchestrator_needs_search") else "generate"
        
    workflow.add_conditional_edges(
        "orchestrator_service",
        orchestrator_condition,
        {
            "search": "orchestrator_web_search",
            "generate": "question_generator"
        }
    )
    
    workflow.add_edge("orchestrator_web_search", "orchestrator_service")
    
    def refinement_loop_condition(state: InterviewState):
        return "evaluate" if state.get("question_ready") else "check_db"
        
    workflow.add_conditional_edges(
        "question_generator",
        refinement_loop_condition,
        {
            "evaluate": "answer_evaluator",
            "check_db": "check_db_indexes"
        }
    )
    
    def db_index_condition(state: InterviewState):
        return "refine" if state.get("indexes_present_in_db") else "web_search"
        
    workflow.add_conditional_edges(
        "check_db_indexes",
        db_index_condition,
        {
            "refine": "refine_question",
            "web_search": "refine_web_search"
        }
    )
    
    workflow.add_edge("refine_web_search", "refine_question")
    # Loop back to generator to proceed to evaluator
    workflow.add_edge("refine_question", "question_generator")
    
    def end_condition(state: InterviewState):
        count = state.get("current_q_count", 0)
        max_q = state.get("max_q_count", 3) # default max 3
        return "end" if count >= max_q else "next_question"
        
    workflow.add_conditional_edges(
        "answer_evaluator",
        end_condition,
        {
            "end": "generate_report",
            "next_question": "question_generator"
        }
    )
    
    workflow.add_edge("generate_report", END)
    
    return workflow
