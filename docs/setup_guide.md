# Backend Setup Guide: Agentic AI Interviewer

This guide walks you through setting up and running the FastAPI & LangGraph backend for the Agentic AI Interviewer.

## 1. Prerequisites

Before you begin, ensure you have the following installed and available:
- **Python 3.11+**
- **Conda** (or another virtual environment manager)
- **Redis Server** (or an Upstash Redis URL)
- **PostgreSQL Database** (e.g., NeonDB)
- API Keys for:
  - **Groq** (LLM Inference)
  - **Tavily** (Agentic Web Search)
  - **LangSmith** (optional, for LangGraph tracing/monitoring)

---

## 2. Installation

1. **Activate your Conda environment** (or create a new one):
   ```bash
   conda activate venv
   ```

2. **Navigate to the backend directory**:
   ```bash
   cd backend
   ```

3. **Install the required Python packages**:
   ```bash
   pip install -r requirements.txt
   ```

---

## 3. Environment Variables

1. Copy the example `.env` file to create your local `.env`:
   ```bash
   cp .env.example .env
   ```
2. Open `backend/.env` and fill in your actual API keys and connection URLs:
   - `GROQ_API_KEY`: Your Groq API key.
   - `TAVILY_API_KEY`: Your Tavily API key.
   - `REDIS_URL`: Your Redis connection string (e.g., Upstash).
   - `DATABASE_URL`: Your PostgreSQL connection string (e.g., NeonDB).
   - *Optional:* Fill in `LANGCHAIN_API_KEY` and set `LANGCHAIN_TRACING_V2=true` for LangSmith monitoring.

---

## 4. Database Setup (Prisma & NeonDB)

Since we are using Prisma ORM with Python to connect to PostgreSQL:

1. **Generate the Prisma client**:
   ```bash
   prisma generate
   ```
2. **Push the schema to your database** (this creates the necessary tables in NeonDB):
   ```bash
   prisma db push
   ```
   *(Note: If you want to use migrations instead, you can run `prisma migrate dev`).*

---

## 5. Running the Backend Architecture

The backend consists of three independent processes: the FastAPI HTTP/WebSocket server, a background worker for parsing documents, and a background worker for writing to the database. 

You must run these in **three separate terminal windows/tabs**. Ensure you are in the `backend/` directory and your conda `venv` is activated in **all three**.

**Terminal 1: Start the API Server**
```bash
uvicorn app:app --reload --port 8000
```
*(Runs the FastAPI app on http://localhost:8000)*

**Terminal 2: Start the Ingestion Worker**
```bash
python src/agentic_ai_interviewer/workers/ingest_worker.py
```
*(Listens to the `parsing_queue` to embed Resumes/JDs via FAISS)*

**Terminal 3: Start the Database Worker**
```bash
python src/agentic_ai_interviewer/workers/db_worker.py
```
*(Listens to the `db_write_queue` to securely save completed interviews to NeonDB)*

---

## 6. Accessing the App

Once all three terminal processes are running successfully:

- **API Docs:** You can view the Swagger UI for the endpoints at `http://localhost:8000/docs`
- **LangSmith Tracing:** If configured, you can monitor the AI agent's execution graphs by visiting your LangSmith project dashboard.