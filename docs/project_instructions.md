# Project Context: Agentic AI Interviewer Backend

## Overview
You are an expert Python Backend Engineer and AI Architect. Your task is to build the backend for an advanced, agentic AI Interviewer. 

The system conducts real-time, text-based technical interviews. It ingests a Resume and Job Description (JD), orchestrates the interview context using a FAISS Vector Database, dynamically generates and refines questions using Groq for ultra-fast inference, waits for human input via WebSockets, and evaluates answers. 

## 1. Tech Stack Requirements
* **Core Logic:** Python 3.11+
* **API Framework:** FastAPI (utilizing WebSockets for real-time interaction).
* **Agent Orchestration:** LangGraph (specifically `StateGraph`).
* **LLM Integration:** Groq (using `ChatGroq` from `langchain-groq` for high-speed inference).
* **Vector Database:** FAISS (using `langchain-community` FAISS integration) for local, in-memory vector storage and retrieval of parsed Resume/JD chunks. Utilize HuggingFace embeddings (e.g., `all-MiniLM-L6-v2`) to keep it entirely local and fast.
* **State Persistence:** Redis (via LangGraph's checkpointer) to persist the graph state during the `interrupt_before` pauses. 
* **Tools:** Tavily Search API (using `TavilySearchResults` from `langchain-community`) for the agentic web search nodes.

## 2. The Graph State Definition (`InterviewState` TypedDict)
```python
from typing import TypedDict, List, Dict, Any

class InterviewState(TypedDict):
    # Identifiers
    session_id: str
    
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
```

## 3. Node Implementations (Async Python Functions)
Implement these using `ChatGroq` for all LLM decisions and generation:
1.  **`ingest_documents`**: Takes raw Resume/JD text, chunks them (e.g., `RecursiveCharacterTextSplitter`), embeds them, and stores them in the FAISS index with metadata (`source: resume` or `source: jd`).
2.  **`orchestrator_service`**: Queries FAISS for core requirements. Uses Groq to decide if it lacks context on a specific tech stack, setting `orchestrator_needs_search=True` if so.
3.  **`orchestrator_web_search`**: Uses Tavily to find missing context and returns the summary.
4.  **`question_generator`**: Looks at previous `evaluations`. Formulates a `search_query` to pull relevant facts from FAISS. Generates a `draft_question`. Sets `question_ready=True` if arriving from the refinement loop.
5.  **`check_db_indexes`**: Evaluates if the `draft_question` relies on obscure knowledge not in FAISS. Returns `indexes_present_in_db`.
6.  **`refine_web_search`**: Uses Tavily to search the web for the obscure technical concept.
7.  **`refine_question`**: Takes the `draft_question` and Tavily results to output a fact-checked `final_question`.
8.  **`answer_evaluator`**: **CRITICAL:** The graph will `interrupt_before` this node. Once resumed with a `human_answer`, it evaluates the answer, scores it, appends to `evaluations`, and increments `current_q_count`.
9.  **`generate_report`**: Synthesizes all `evaluations` into a final markdown report.

## 4. Graph Routing & Topology
* **Start**: `ingest_documents` -> `orchestrator_service`.
* **Orchestrator Loop**: If `orchestrator_needs_search`, route to `orchestrator_web_search` -> `orchestrator_service`. Else, route to `question_generator`.
* **Refinement Loop**: If `question_ready`, route to `answer_evaluator`. Else, route to `check_db_indexes`.
    * From `check_db_indexes`: If `indexes_present_in_db`, route to `refine_question`. Else, route to `refine_web_search` -> `refine_question`.
    * From `refine_question` route back to `question_generator`.
* **Condition Check**: From `answer_evaluator`: if `current_q_count >= max_q_count`, route to `generate_report` -> `END`. Else, route to `question_generator`.

**CRITICAL:** Compile the graph with `interrupt_before=["answer_evaluator"]` and attach the Redis checkpointer.

## 5. API & WebSocket Layer (FastAPI)
1.  `POST /init_interview`: Accepts Resume and JD strings/files. Generates a `session_id`. Runs the graph asynchronously up to the first interrupt. Returns the `session_id`.
2.  `WebSocket /ws/interview/{session_id}`: 
    * On connect, fetch the current graph state from Redis using the `session_id`. Stream the `final_question` to the client.
    * Wait for the client's text message.
    * Update state with `{"human_answer": message}` and resume the graph.
    * The graph loops, generates the next question, and hits the interrupt again.
    * Stream the new `final_question`.
    * If graph reaches `END`, stream the `final_report` and close connection.

## 6. Execution Instructions for Claude
1.  Initialize the project structure .
2.  Write the complete, runnable asynchronous Python code fulfilling all requirements above.
3.  Implement the FAISS vector store logic cleanly, ensuring the index is accessible to the nodes.
4.  Provide a clear `requirements.txt` (including `langgraph`, `fastapi`, `langchain-groq`, `faiss-cpu`, `tavily-python`, `redis`, etc.).
5.  Include a `.env.example` file specifying `GROQ_API_KEY`, `TAVILY_API_KEY`, and `REDIS_URL`.