from typing import TypedDict, List, Dict, Any


class InterviewState(TypedDict):
    # Identifiers & Timing
    session_id: str
    start_time: float  # Unix timestamp initialized at session start

    # Raw Document Input (passed during /init_interview)
    resume_text: str
    jd_text: str

    # JD Topic Tracking (adaptive coverage)
    jd_topics: List[str]  # Comprehensive list of skills/topics extracted from JD
    covered_topics: List[str]  # Topics that have already been evaluated

    # Vector DB Context
    search_query: str
    retrieved_context: List[str]

    # Orchestrator Context
    orchestrator_needs_search: bool
    orchestrator_search_results: str

    # Question Generation
    draft_question: str
    current_difficulty: str  # "Easy", "Medium", or "Hard" — default "Medium"
    final_question: str
    question_ready: bool

    # Human Input & Evaluation
    human_answer: str
    evaluator_feedback: str  # Context passed from evaluator to generator for next question
    evaluations: List[Dict[str, Any]]
    # Format: {"q": ..., "a": ..., "score": ..., "difficulty": ..., "topic_tested": ..., "feedback": ...}

    # Hard Stop
    user_requested_stop: bool  # default False — set True when user wants to end

    # Socratic Failure Protocol
    requires_hint: bool  # default False — set True when answer score <= 5
    failed_condition_context: str  # describes the specific conceptual gap for follow-up

    # Final Output
    final_report: str
