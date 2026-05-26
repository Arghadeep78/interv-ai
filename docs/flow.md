# Interview Flow Perspective

This document outlines the end-to-end flow of the Agentic AI Interviewer, visualizing the experience from the candidate's perspective and mapping it to the underlying system architecture.

## 1. Preparation & Initialization

**Candidate Action:** The candidate visits the web application and uploads their Resume and the Job Description (JD) they are applying for.

**System Process:**
* The React frontend makes a `POST /init_interview` request with the uploaded documents.
* The backend generates a unique `session_id`.
* **Document Ingestion:** The agent processes the Resume and JD, breaking them down into searchable chunks and embedding them into a session-scoped FAISS vector store.
* **Topic Extraction & Enrichment:** `orchestrator_service` extracts key topics from the JD. If additional context is needed, `orchestrator_web_search` triggers a Tavily web search to augment knowledge on the company or specific technical terms.
* The frontend polls the server until the session is ready, then establishes a WebSocket connection.

## 2. The Interview Start (Icebreaker)

**Candidate Action:** The candidate waits briefly, and the AI initiates the conversation.

**System Process:**
* **First Question Generation:** The LangGraph state machine routes to `question_generator` in **Mode C (Icebreaker)**.
* A Light LLM (Llama 3.1 8B) looks at the resume and generates a warm, open-ended question (e.g., *"Tell me about yourself..."*).
* The question streams back to the frontend with a typewriter effect for a natural feel.

## 3. The Q&A Loop (Core Interview)

**Candidate Action:** The candidate types their response to the AI's question and hits send.

**System Process:**
* The answer is sent over the WebSocket to the LangGraph execution block.
* **Evaluation (`answer_evaluator`):** A Heavy LLM (Llama 3.3 70B) evaluates the response:
    * **Scoring:** Assigns a score (1–10) based on accuracy, depth, and communication.
    * **Skip Detection:** Detects if the candidate asked to skip or didn't know the answer.
    * **Difficulty Adjustment:** Dynamically scales the difficulty of upcoming questions (Easy, Medium, Hard) depending on the candidate's performance.
    * **Hints:** If the answer is on the wrong track, it sets up a Socratic hint rather than revealing the answer straight away.
* **Appreciation & Transition (`generate_appreciation`):** The system generates a brief acknowledgment or warm transition based on the evaluation before proceeding.
* **Next Question Generation (`question_generator`):** 
    * If a hint is needed, **Mode B (Socratic Hint)** activates to present a concrete failing scenario based closely on the candidate's answer.
    * Otherwise, **Mode D (Normal Question)** leverages the Heavy LLM to ask an adaptive technical question on the next extracted JD topic.

## 4. Candidate Interaction & Interrupts

### Handling Stops
**Candidate Action:** The candidate says *"I have to go"* or *"Let's stop here."*
**System Process:** 
* `question_generator` running in **Mode A (Stop Detection)** identifies the intent to conclude.
* The graph instantly bypasses the remaining topics and pivots to the reporting phase.

### Handling Disconnects
**Candidate Action:** The candidate accidentally closes the tab or loses internet connection.
**System Process:**
* State and interview history are securely persisted on the server (Redis). 
* The candidate can reconnect, and the system restores exactly where they left off without data loss.

## 5. Conclusion & Reporting

**Candidate Action:** The interview completes (either the time goes over 40 minutes, all topics are covered, or the candidate stops the interview). The candidate waits a moment for the final decision.

**System Process:**
* The LangGraph routes to the **`generate_report`** node.
* A Heavy LLM synthesizes the entire conversation history, scoring metrics, and JD alignment into a comprehensive Markdown report.
* The frontend formats and renders the report to the candidate.
* Concurrently, the candidate's report and metrics are pushed to an async queue (ARQ) for permanent storage in PostgreSQL.
* The interview session terminates successfully.
