# Setup (Newbie Friendly)

This guide gets the project running locally with the least friction. If you want the more detailed backend guide, see docs/setup_guide.md.

## 0) Prerequisites

Install these first (what each is used for):
- Python 3.11+ - runs the FastAPI backend, LangGraph logic, and workers
- Node.js 18+ - runs the React + Vite frontend
- Redis (local or hosted) - session status and job queue for background workers
- PostgreSQL (local or hosted, e.g. Neon) - stores final interview reports and metadata
- Groq API key - LLM inference for questions, evaluation, and report generation
- Tavily API key - web search to enrich missing context
- LangSmith API key (optional) - tracing and monitoring for LangGraph/LangChain

## 1) Environment files

### Backend .env (required)

From the repo root:

```bash
cd backend
cp .env.example .env
```

Open backend/.env and fill in:
- GROQ_API_KEY - used by the LLM client
- TAVILY_API_KEY - used by the web search node
- REDIS_URL - used for session status and worker queues (local: redis://localhost:6379)
- DATABASE_URL - PostgreSQL connection string used by Prisma
- Optional: LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY, LANGCHAIN_PROJECT - LangSmith tracing

### Frontend .env (optional)

The frontend does not require any env variables right now. If you want a placeholder file, create frontend/.env (it can be empty). The backend URL is currently hardcoded in frontend/src/App.tsx.

## 2) Start Redis and Postgres

You can use local services or hosted ones. Either way, you will need their connection URLs in the backend .env file.

If you run local Redis, make sure it is up before starting the backend.

## 3) Backend Setup

From the repo root:

> **Use one Python, always activate the venv.** Run every backend command (uvicorn, workers, pip, prisma) from inside the same activated `.venv`. If you sometimes start uvicorn from the venv and other times from a system/Homebrew Python (e.g. 3.12 vs 3.14), you can end up with **two servers bound to port 8000 at once**. Only one owns the socket, so requests route inconsistently and the frontend hangs on "INITIALIZING". Before starting the API server, confirm nothing is already on 8000 (see "Two servers on port 8000" in Troubleshooting).

### Option A: venv

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Option B: conda

```bash
conda create -n venv python=3.11
conda activate venv
```

### Install dependencies

```bash
cd backend
pip install -r requirements.txt
```

### Prisma database setup

```bash
prisma generate
prisma db push
```

If the prisma command is not found, make sure your virtual environment is active and that pip install -r requirements.txt completed without errors.

## 4) Run the Backend (4 terminals)

You need 4 terminals running simultaneously. Each terminal should have the Python venv activated. Open 4 separate terminals and run them in order:

### Terminal 1 (Redis)
```bash
redis-server
```
(Or skip if using hosted Redis. Leave running.)

### Terminal 2 (API server, HTTP + WebSocket)
```bash
cd backend
source ../.venv/bin/activate
python --version          # sanity check: should be the SAME version every time
uvicorn app:app --reload --port 8000
```
Leave this running. It powers the network APIs and WebSocket connections.
API docs: http://localhost:8000/docs

> Start the API server **only once**. Do not launch a second uvicorn (e.g. from a different terminal or a different Python install) on port 8000 — see "Two servers on port 8000" in Troubleshooting.

### Terminal 3 (Ingest worker, builds FAISS from resume/JD)
```bash
cd backend
source ../.venv/bin/activate
python src/agentic_ai_interviewer/workers/ingest_worker.py
```
Leave this running. It processes uploaded resumes/JDs and generates the local FAISS vector stores for semantic search.

### Terminal 4 (DB worker, writes reports to Postgres)
```bash
cd backend
source ../.venv/bin/activate
python src/agentic_ai_interviewer/workers/db_worker.py
```
Leave this running. It writes final interview reports to PostgreSQL via Prisma.

## 5) Frontend Setup (Terminal 5)

Open a new terminal at the repo root:

```bash
cd frontend
npm install
npm run dev
```

Vite will print the local URL (usually http://localhost:5173).

## 6) Use the App

Once all 5 terminals are running with no errors:

1. Open http://localhost:5173 in your browser.
2. Upload a resume and a job description (PDF or TXT).
3. Click "Launch Interview" and wait for the status to turn ready.
4. Click "COMMENCE" to start the interview.
5. Answer the AI's questions and receive real-time feedback.
6. View the final report with your scores and topic coverage.

## Troubleshooting

- **Connection timeout on "Launch Interview"**: Make sure Terminal 2 (API server) is running and listening on port 8000. Check for errors in that terminal.
- **Stuck on "INITIALIZING" / requests hang randomly (two servers on port 8000)**: This happens when more than one uvicorn process is bound to port 8000 — commonly when one was started from the `.venv` and another from a system/Homebrew Python (different versions, e.g. 3.12 and 3.14). Only one can own the socket, so requests route inconsistently and the frontend never leaves "INITIALIZING". Check what's listening and which Python it is:
  ```bash
  lsof -nP -iTCP:8000 -sTCP:LISTEN
  ```
  If you see more than one server (e.g. one bound to `*:8000` and another to `127.0.0.1:8000`, or two different `Python` paths), kill the extra one by its PID:
  ```bash
  kill <PID>
  ```
  Then start the API server exactly once from the activated `.venv` (Terminal 2 above). To avoid this entirely, always activate the same `.venv` before running `uvicorn` and never start a second instance.
- **Stuck on processing**: Confirm Terminal 1 (Redis) is running and REDIS_URL in .env is correct.
- **Missing worker processes**: Ensure Terminal 3 (ingest worker) and Terminal 4 (DB worker) are both running without errors.
- **Database errors**: Verify DATABASE_URL in .env and that `prisma db push` succeeded (section 3).
- **WebSocket errors**: Make sure the backend (Terminal 2) is running on port 8000 with `--reload` flag enabled.
- **Missing packages**: Confirm the Python venv is active (you should see `.venv` in your prompt). If needed, rerun `pip install -r requirements.txt` in the backend directory.
- **npm install fails**: Make sure Node.js 18+ is installed. Run `node --version` to check.
- **Workers not starting**: Check that your .env file exists and has REDIS_URL, DATABASE_URL, GROQ_API_KEY, and TAVILY_API_KEY filled in.

## Helpful Docs

- docs/setup_guide.md (backend details)
- docs/architecture.md (system overview)
