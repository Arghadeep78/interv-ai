# Interv AI — Frontend

The web client for the **Agentic AI Interviewer**. A React + Vite + TypeScript single-page
app that lets a candidate upload their Resume and a Job Description, then conducts the
interview in a real-time chat interface backed by the FastAPI + LangGraph backend.

## Stack

- **Framework:** React 19 + Vite + TypeScript
- **Styling:** Tailwind CSS (+ `@tailwindcss/typography`) and Lucide React icons
- **Routing:** React Router
- **Real-time:** Native WebSockets
- **HTTP client:** Axios
- **Markdown:** `react-markdown` + `remark-gfm` (renders the final interview report)

## How it talks to the backend

1. `POST /init_interview` — uploads the Resume + JD, receives a `session_id`.
2. `GET /status/{session_id}` — polled until the session is `ready`.
3. `ws://…/ws/interview/{session_id}` — the live interview: the client sends answers and
   receives typed messages (`question`, `evaluation`, `status`, `rate_limit`, `report`).

## Session persistence

Key state is written to `sessionStorage` on every change so an accidental page refresh
doesn't send the candidate back to the home screen:

| Key | Contents |
|---|---|
| `intervai_session_id` | The active `session_id` |
| `intervai_status` | Current interview status (`idle` / `ready` / `interviewing` / `ended` / …) |
| `intervai_messages` | Full chat history as JSON |
| `intervai_elapsed` | Elapsed interview time in seconds |

On mount the app restores these values. If the saved status is `interviewing`, a new
WebSocket is opened to the same `session_id` automatically so the interview continues
seamlessly. Closing the browser tab clears `sessionStorage`, so re-opening the app starts
fresh. A **New Interview** button in the header (visible once an interview ends) explicitly
clears storage and resets all state.

## Configuration

Backend endpoints are read from a Vite env var so the app can be deployed without code
changes. Copy the example and set the URL:

```bash
cp .env.example .env
```

```bash
# .env
VITE_API_URL=http://localhost:8000
```

`src/config.ts` reads `VITE_API_URL` and derives the WebSocket URL from it
(`http` → `ws`, `https` → `wss`), so you only set the one variable. It falls back to
`http://localhost:8000` for local development.

## Getting started

```bash
npm install
npm run dev      # start the Vite dev server (default http://localhost:5173)
```

Make sure the backend is running and reachable at `VITE_API_URL` (see the
[Backend Setup Guide](../docs/setup_guide.md)).

## Scripts

| Command | Description |
|---|---|
| `npm run dev` | Start the dev server with HMR |
| `npm run build` | Type-check (`tsc -b`) and build for production |
| `npm run preview` | Preview the production build locally |
| `npm run lint` | Run ESLint |

## Project docs

- [Architecture Overview](../docs/architecture.md)
- [Interview Flow](../docs/flow.md)
- [Setup & Installation Guide](../docs/setup_guide.md)
