# Architecture Overview

The **Agentic AI Interviewer** is structured as a decoupled, event-driven system: a React frontend communicates over WebSockets with a FastAPI backend, which drives a LangGraph state machine backed by Groq LLMs, FAISS vector stores, Redis, and PostgreSQL.

---

## System Diagram

```mermaid
graph TD
    A["/init_interview (Resume + JD)"] --> B["ingest_documents\nChunk & embed Resume/JD → FAISS"]
    B --> C["orchestrator_service\nExtract JD topics · Decide if search needed"]
    C -->|needs web context| D["orchestrator_web_search\nTavily API · Embed results into FAISS"]
    D --> C
    C -->|ready| E["question_generator"]

    E -->|Q1 — Icebreaker| E1["Light LLM 8B\nTell me about yourself..."]
    E -->|Stop detected| E2["Wrap-up message\nuser_requested_stop = true"]
    E -->|Socratic hint needed| E3["Heavy LLM 70B\nPresent failing scenario"]
    E -->|Normal question| E4["Heavy LLM 70B\nAdaptive technical question"]

    E1 --> F
    E2 --> G
    E3 --> F
    E4 --> F

    F["answer_evaluator ⏸️ INTERRUPT\nScore · Skip detection · Difficulty adjustment"]
    F -->|time < 40 min AND topics remain| E
    F -->|time >= 40 min OR all topics covered| G
    F -->|user_requested_stop| G

    G["generate_report\nMarkdown report · ARQ → PostgreSQL"]
    G --> H["END"]
```

---

## Component Breakdown

### 1. Client — React / Vite / TypeScript
- Uploads Resume & JD via `POST /init_interview`.
- Polls `GET /status/{session_id}` until the session is ready.
- Opens a WebSocket and renders a chat-like interface with typewriter-effect streaming, evaluation badges, and the final report.
- **Disconnect Resilience:** If the WebSocket connection drops, state is securely persisted on the server, permitting reconnections without data loss.

### 2. FastAPI Server (`app.py`)
- **HTTP layer:** Generates `session_id`, streams documents to FAISS, and seeds Redis with session state.
- **WebSocket layer:** Acts as the bridge between the frontend and the LangGraph state machine — injects human answers into the graph state and routes typed messages (`question`, `evaluation`, `status`, `rate_limit`, `report`) to the client.
- **Rate-limit resilience:** Transient 429 errors send a friendly `rate_limit` message and wait 15s without closing the connection. If `RateLimitExhaustedError` is raised (all 5 retries across all fallback models fail), a partial report is generated from completed evaluations and the session is concluded gracefully.

### 3. LangGraph Agentic Orchestrator

#### Nodes

| Node | Model | Description |
|---|---|---|
| `ingest_documents` | — | Chunks Resume + JD, embeds via HuggingFace `all-MiniLM-L6-v2`, stores in session-scoped FAISS index |
| `orchestrator_service` | Light (8B) | Extracts `jd_topics` from JD context; decides if web search is needed |
| `orchestrator_web_search` | — | Runs Tavily query; embeds results back into FAISS for use by later nodes |
| `question_generator` | Light/Heavy | **Four operational modes** (see below) |
| `answer_evaluator` | Heavy (70B) | Scores answer 1–10, detects skip intent, adjusts difficulty, triggers hint or burns topic |
| `generate_report` | Heavy (70B) | Produces a structured Markdown report; enqueues async DB write via ARQ |

#### Question Generator — Four Modes

```
Mode A  Stop Detection   [Light 8B]  Classifies if the candidate wants to end the interview
Mode B  Socratic Hint    [Heavy 70B] Presents a concrete failing scenario without revealing the answer
Mode C  Icebreaker       [Light 8B]  First question only — warm opener about background/experience
Mode D  Normal Question  [Heavy 70B] Topic-targeted technical question at the current difficulty level
```

#### Graph Routing Flow
The state machine implements a dual-termination strategy bounded by a 40-minute duration limit or complete topic coverage, paired with dynamic capability routing.
1. Document ingestion and topic evaluation loops with web searches until sufficient topic mastery is gained.
2. The `answer_evaluator` leverages the Human-in-the-loop interruption pause.
3. Depending on evaluation: candidate performance dynamically alters difficulty (Easy, Medium, Hard). Socratic hint nodes loop backwards without topic burn until answered correctly or skipped. 
4. Stop sequences instantly pivot the routing straight into reporting.

### 4. LLM Engine — Groq + Tenacity

#### Model Routing Strategy
- **Heavy** (`llama-3.3-70b-versatile`): Question generation, answer evaluation, report writing.
- **Medium** (`mixtral-8x7b-32768`): Automatic fallback when heavy model hits rate limits.
- **Light** (`llama-3.1-8b-instant`): Icebreaker, stop detection, topic extraction, search decisions.

#### Retry Layer (`LLM/__init__.py`)
Utilizes exponential backoff (2s → 60s max) combined with an orchestrated 3-tiered fallback chain to handle rate limiting and API exhaustion safely on a node level.

### 5. Token Optimization Measures
| Technique | Impact |
|---|---|
| Light model for classifiers | ~5× cheaper per classifier call |
| Condensed prompts | ~30–40% fewer tokens per LLM call |
| `max_tokens` caps | 5 tokens for classifiers, 256 for light generation, 512–1024 for heavy evaluation |
| Sliding-window history | Only the last 5 evaluations are sent as context |
| FAISS context truncation | Retrieved chunks capped at 400 chars; `k` reduced dynamically across all nodes |
| Search context capping | Max 2 results fetched for questions, 1 for evaluation |

### 6. State Machine Schema (`InterviewState`)
21 state fields maintain the progression context, including new behavioral modifiers:
```python
start_time                 # Evaluates 40-minute limits
current_difficulty         # Tracks and shifts scaling difficulty (Easy, Medium, Hard)
user_requested_stop        # Hard stop flag — bypasses all routing checks
requires_hint              # Socratic failure trigger
failed_condition_context   # Employs previous answer to format failure-scenario based targeted Socratic hints
covered_topics             # Records tested nodes ensuring no re-testing of the same metric across interview lifespan
```
*Facilitated natively using `AsyncRedisSaver` for efficient scaling across worker tasks and thread retention during potential timeouts.*

### 7. Background Workers
- **ARQ DB Worker:** Listens on Redis for `process_db_write` jobs enqueued by `generate_report`. Writes transcripts, scores, and reports to PostgreSQL (NeonDB via Prisma) asynchronously without blocking API event loops.

## Directory Structure
- `backend/app.py`: FastAPI entry point, WebSocket bridging, and async worker binding.
- `backend/src/agentic_ai_interviewer/`: Context root module.
  - `graph/`: LangGraph structural orchestration logic (`build_graph()`).
  - `nodes/`: Operational components dictating the LangGraph step execution (ingestion, question generator, answer evaluations, searches).
  - `LLM/`: Advanced ChatGroq model configurations paired natively with Tenacity fallback wrappers.
  - `tools/`: FAISS index manipulation and web API calls.
  - `workers/`: Background Redis jobs logic.
- `frontend/src/App.tsx`: Real-time streaming Vite interfaces.
- `docs/`: Deployment instruction sets, architectural logic details, and update tracking records.
