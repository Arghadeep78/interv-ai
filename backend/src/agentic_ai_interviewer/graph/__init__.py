import time
from langgraph.graph import StateGraph, END
from agentic_ai_interviewer.state import InterviewState
from agentic_ai_interviewer.nodes import (
    ingest_documents,
    orchestrator_service,
    orchestrator_web_search,
    question_generator,
    answer_evaluator,
    generate_report,
)


def build_graph() -> StateGraph:
    """
    Builds the LangGraph StateGraph for the AI Interviewer.

    Topology:
        ingest_documents → orchestrator_service → [needs_search?]
            YES → orchestrator_web_search → orchestrator_service
            NO  → question_generator → answer_evaluator (INTERRUPT)
                    → [check_interview_status]
                        CONTINUE → question_generator
                        END      → generate_report → END
    """
    workflow = StateGraph(InterviewState)

    # 1. Add all nodes
    workflow.add_node("ingest_documents", ingest_documents)
    workflow.add_node("orchestrator_service", orchestrator_service)
    workflow.add_node("orchestrator_web_search", orchestrator_web_search)
    workflow.add_node("question_generator", question_generator)
    workflow.add_node("answer_evaluator", answer_evaluator)
    workflow.add_node("generate_report", generate_report)

    # 2. Set entry point and edges
    workflow.set_entry_point("ingest_documents")
    workflow.add_edge("ingest_documents", "orchestrator_service")

    # Orchestrator conditional: search or proceed to question generation
    def orchestrator_condition(state: InterviewState) -> str:
        if state.get("orchestrator_needs_search"):
            return "search"
        return "generate"

    workflow.add_conditional_edges(
        "orchestrator_service",
        orchestrator_condition,
        {
            "search": "orchestrator_web_search",
            "generate": "question_generator",
        },
    )

    # Orchestrator search loops back to orchestrator
    workflow.add_edge("orchestrator_web_search", "orchestrator_service")

    # Question generator always goes to answer evaluator
    workflow.add_edge("question_generator", "answer_evaluator")

    # After evaluation: check if interview should end
    def check_interview_status(state: InterviewState) -> str:
        """
        Two termination conditions:
        1. Time Check: 40 minutes (2400 seconds) elapsed
        2. Topic Check: all jd_topics have been covered
        """
        start_time = state.get("start_time", 0)
        jd_topics = state.get("jd_topics", [])
        covered = state.get("covered_topics", [])

        # Time check: 40 minutes
        if start_time and (time.time() - start_time) >= 2400:
            return "end"

        # Topic check: all topics covered
        if jd_topics and all(topic in covered for topic in jd_topics):
            return "end"

        return "continue"

    workflow.add_conditional_edges(
        "answer_evaluator",
        check_interview_status,
        {
            "end": "generate_report",
            "continue": "question_generator",
        },
    )

    # generate_report → END
    workflow.add_edge("generate_report", END)

    return workflow
