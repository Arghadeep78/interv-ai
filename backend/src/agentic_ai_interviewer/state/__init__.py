from typing import TypedDict, List, Dict, Any, Optional

class InterviewState(TypedDict):
    # Identifiers
    session_id: str
    
    # JD Extract Context
    jd_skills: List[str]
    
    # Vector DB Context
    search_query: str 
    retrieved_context: List[str]
    
    # Orchestrator Context
    orchestrator_needs_search: bool 
    orchestrator_search_results: str
    
    # Question Generation Loop
    draft_question: str
    indexes_present_in_db: bool
    refinement_search_results: str
    final_question: str
    question_ready: bool 
    
    # Human Input & Evaluation
    human_answer: str
    evaluations: List[Dict[str, Any]] # Format: {"q": ..., "a": ..., "score": ..., "feedback": ...}
    
    # Loop Conditions
    current_q_count: int
    max_q_count: int
    final_report: str
