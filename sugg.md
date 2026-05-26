# Architecture Review & Suggestions — Interv AI

A focused, practical review for an SDE-1 resume project. Only changes worth doing
are listed — ordered by impact. Each item says **what's wrong**, **why it matters**,
and **the fix**, tagged with a **priority** (P0 = do now, P1 = soon, P2 = nice-to-have)
and a rough **time estimate**.

> **Jump to the [prioritized plan](#prioritized-plan) at the bottom** for the do-this-order list.

---

## 1. Bugs that will break things in front of an interviewer

These are the ones a reviewer will catch immediately. Fix these first.

### 1.1 State is NOT actually persisted (contradicts your own docs)  `[P0 · ~1–2h]`
`backend/app.py:57` compiles the graph with an **in-memory** checkpointer:

```python
checkpointer = MemorySaver()
```

But `app.py:381` comments claim *"the graph state is persisted in Redis ... client can
reconnect and resume at any time"*, and `requirements.txt` ships
`langgraph-checkpoint-redis`. With `MemorySaver`, **every session is lost on restart**
and reconnect-resume only works within one process. This is the single biggest gap
between what the code says and what it does.

**Fix:** swap to the Redis checkpointer you already depend on:

```python
from langgraph.checkpoint.redis import AsyncRedisSaver
checkpointer = AsyncRedisSaver.from_conn_string(redis_url)  # see lib docs for exact API
```

### 1.2 `extractedSkills` is always saved empty  `[P0 · ~2min]`
`backend/src/agentic_ai_interviewer/workers/db_worker.py:29`:

```python
"extractedSkills": json.dumps(state.get("jd_skills", []))
```

The state key is `jd_topics`, never `jd_skills`. So the DB column is always `[]`.
**Fix:** `state.get("jd_topics", [])`.

### 1.3 Broken JSX in the report summary  `[P0 · ~5min]`
`frontend/src/App.tsx:396-400` — the `className` template literal is malformed (a stray
`>` lands inside the string and the `text-danger` branch is never closed). The average
score color never applies and the markup is fragile.

```tsx
<p className={`text-3xl font-display ${
  m.summary.average_score >= 7 ? 'text-accent' :
  m.summary.average_score >= 5 ? 'text-warning' : 'text-danger'}>`
  {m.summary.average_score}/10
```

**Fix:** close the template literal and `className` properly:

```tsx
<p className={`text-3xl font-display ${
  m.summary.average_score >= 7 ? 'text-accent'
  : m.summary.average_score >= 5 ? 'text-warning'
  : 'text-danger'}`}>
  {m.summary.average_score}/10
</p>
```

### 1.4 A model in the fallback chain is decommissioned  `[P0 · ~10min]`
`backend/src/agentic_ai_interviewer/LLM/__init__.py:33` lists `mixtral-8x7b-32768`.
Groq retired this model — any call that falls back to it returns a 400, which your
rate-limit handler will misread. **Fix:** replace with a current Groq model (e.g.
another Llama/Gemma variant) or drop the medium tier and fall back heavy→light only.

---

## 2. Performance — the interview will feel slow

### 2.1 FAISS + embedding model reloaded from disk on every node  `[P1 · ~2–3h]`
`tools/vectorstore.py` re-instantiates `HuggingFaceEmbeddings(...)` and calls
`FAISS.load_local(...)` on *each* node invocation (`load_faiss_index` is called in
`orchestrator_service`, `question_generator`, `answer_evaluator`...). Loading the
sentence-transformer model is expensive and happens many times per question.

**Fix (highest perf win):**
- Cache the embeddings model as a module-level singleton (load once).
- Keep the per-session FAISS store in an in-process LRU dict keyed by `session_id`
  instead of reading from disk every call.

### 2.2 Tavily web search runs on almost every turn  `[P1 · ~1h]`
`question_generator` and `answer_evaluator` both call Tavily. The evaluator searches
*"Verify: <question> correct answer"* on **every single answer**. That's a network
round-trip (and cost) injected into the critical path of each turn, plus it bloats the
FAISS index. **Fix:** gate search behind a real need (e.g. only for Hard questions, or
only when local context is empty), and cache results per topic.

### 2.3 The whole graph runs synchronously inside `POST /init_interview`  `[P2 · ~2h (or ~5min to delete dead worker)]`
`app.py:142` streams the full ingest→orchestrator→first-question pipeline *inside the
HTTP request*, while a perfectly good `ingest_worker.py` (ARQ) sits **unused**. The
upload request blocks for seconds. **Fix:** either enqueue the heavy work to the ARQ
worker (which already updates Redis status — the frontend already polls `/status`), or
keep it sync but delete the dead `ingest_worker.py` so the architecture isn't lying.

---

## 3. Config & deploy correctness

### 3.1 Backend URL is hardcoded in the frontend  `[P1 · ~20min]`
`http://localhost:8000` / `ws://localhost:8000` appear in four places in `App.tsx`
(lines 174, 186, 197). This cannot be deployed. **Fix:** read from
`import.meta.env.VITE_API_URL` and derive the WS URL from it.

### 3.2 CORS config is invalid for credentialed requests  `[P1 · ~10min]`
`app.py:32` sets `allow_origins=["*"]` **and** `allow_credentials=True`. Browsers reject
this combination, and `*` is unsafe anyway. **Fix:** list explicit origins
(`["http://localhost:5173", "<your-prod-domain>"]`).

### 3.3 Deprecated APIs  `[P2 · ~30min]`
- `@app.on_event("startup"/"shutdown")` is deprecated — use a `lifespan` context manager.
- `redis_client.close()` (`app.py:68`) is deprecated — use `await redis_client.aclose()`.

### 3.4 No requirement pinning  `[P1 · ~15min]`
`backend/requirements.txt` pins **nothing**. A fresh install months from now may pull
incompatible LangChain/LangGraph versions (the API surface changes often). **Fix:** pin
versions (`pip freeze` or a `requirements.lock` / `uv` / Poetry).

---

## 4. Security (light touch — it's a demo, but these are quick wins)

### 4.1 `allow_dangerous_deserialization=True` on FAISS load  `[P2 · ~5min (comment only)]`
`vectorstore.py:34` — fine *because you write the indexes yourself*, but note in a
comment that loading untrusted index files executes pickle. Keep session paths
non-user-controlled (they are — UUIDs — good).

### 4.2 No size/type guard on uploads or WS messages  `[P1 · ~30min]`
`extract_text_from_upload` reads the whole file into memory and the WS loop accepts any
text length. A large upload or message is an easy OOM. **Fix:** cap upload size and WS
message length; you already restrict extensions client-side, do it server-side too.

### 4.3 No auth on any endpoint  `[P2 · ~5min to document, ~half-day to implement]`
Anyone can open a session or connect to any `session_id`'s WebSocket. For a portfolio
project this is acceptable, but **say so** in the README rather than leave it implicit.

---

## 5. Code structure / maintainability

### 5.1 Dead / duplicated frontend components  `[P1 · ~30min]`
`frontend/src/components/messages/AiMessage.tsx`, `HumanMessage.tsx`, `EvalBadge.tsx`,
`UploadCard.tsx`, and `hooks/useTypewriter.ts` exist as separate files, but `App.tsx`
**redefines all of them inline** and imports none. Pick one: either use the extracted
components (better — `App.tsx` is 464 lines) or delete the unused files. Right now a
reviewer sees both and wonders which is real.

### 5.2 `faiss_store/` is committed to git and never cleaned up  `[P1 · ~20min]`
There are session index directories checked into the repo (`backend/faiss_store/...`,
`backend/src/faiss_store/...`) — plus indexes get written under **two different base
paths**. **Fix:** add `faiss_store/` to `.gitignore`, remove the committed ones, and
delete a session's index when its report is generated (otherwise disk grows forever).

### 5.3 Huge `eval_prompt` JSON parsing has no schema validation  `[P2 · ~1–2h]`
`answer_evaluator` hand-parses the model's JSON and silently falls back to a hardcoded
`score: 5` on any error. That's a reasonable safety net, but a malformed-but-parseable
response (e.g. score as a string, missing topic) flows straight through. **Fix:** wrap
LLM JSON output in a Pydantic model and use Groq's JSON/structured-output mode so the
shape is guaranteed — this also removes the fragile ```` ``` ```` stripping logic.

### 5.4 `medium` role/`DEFAULT_MAX_TOKENS` are half-wired  `[P2 · ~15min]`
`DEFAULT_MAX_TOKENS` only has `heavy`/`light` keys but code paths reference a `medium`
model. Minor, but tidy the `MODELS`/role mapping so there's one source of truth.

---

## 6. Quick wins (low effort, visible polish)  `[P2 · ~2h total]`

- **Frontend error handling** `[~30min]`: `handleStart`'s `catch {}` just resets to idle
  with no message; `pollStatus` polls forever if status becomes `error:...`. Surface
  failures to the user and stop polling on `error`/timeout.
- **WebSocket reconnect** `[~45min]`: `onclose` just sets `ended`. Given the (intended)
  Redis persistence, a reconnect button would showcase the resilience you describe in
  comments.
- **Magic numbers** `[~15min]`: `2400` (40-min limit) and `3600` (TTL) are inline. Pull
  into named constants / env so the "40-minute interview" is configurable and
  self-documenting.
- **Logging** `[~20min]`: backend uses bare `print(...)` for errors. Use the `logging`
  module you already configured in `LLM/__init__.py` consistently.

---

## Prioritized plan

Work top-down. Priorities: **P0** = correctness bugs / broken claims (do now),
**P1** = needed to deploy or notably improves UX (this week), **P2** = polish & hardening.

### P0 — Bugs & broken claims  (~2–3h total)

| # | Item | Est. | Why it's first |
|---|------|------|----------------|
| 1.3 | Broken JSX in report summary | ~5min | Visibly broken render; trivial fix |
| 1.2 | `extractedSkills` saves empty (`jd_skills`→`jd_topics`) | ~2min | One-word fix, silent data loss |
| 1.4 | Decommissioned model in fallback chain | ~10min | Fallback path 400s in prod |
| 1.1 | Redis checkpointer instead of `MemorySaver` | ~1–2h | Makes the persistence story actually true — biggest credibility win |

### P1 — Deploy-ready & UX  (~5–6h total)

| # | Item | Est. | Why |
|---|------|------|-----|
| 2.1 | Cache embeddings model + FAISS in-process | ~2–3h | Most noticeable speed-up per turn |
| 3.1 | Env-based API/WS URL (drop hardcoded localhost) | ~20min | Can't deploy without it |
| 3.2 | Fix CORS (explicit origins, no `*`+credentials) | ~10min | Browser rejects current combo |
| 2.2 | Gate + cache Tavily searches | ~1h | Cuts latency & API cost on the hot path |
| 5.1 | Remove/adopt duplicated frontend components | ~30min | Repo reads cleanly to a reviewer |
| 5.2 | `.gitignore` + clean `faiss_store/`, delete on report | ~20min | Stops committing artifacts / disk leak |
| 4.2 | Server-side upload + WS size guards | ~30min | Easy OOM otherwise |
| 3.4 | Pin dependency versions | ~15min | Reproducible installs |

### P2 — Polish & hardening  (~6–7h total)

| # | Item | Est. |
|---|------|------|
| 6 | Quick wins (error handling, reconnect, constants, logging) | ~2h |
| 5.3 | Pydantic + structured-output for eval JSON | ~1–2h |
| 2.3 | Async ingestion via ARQ (or delete dead worker) | ~2h / ~5min |
| 3.3 | Replace deprecated `on_event` / `close()` | ~30min |
| 5.4 | Tidy `medium` role / `DEFAULT_MAX_TOKENS` | ~15min |
| 4.1 | Comment the `allow_dangerous_deserialization` rationale | ~5min |
| 4.3 | Document the no-auth assumption (or add auth) | ~5min / ~half-day |

### If you only have an afternoon

Do all of **P0** (~3h) + **3.1, 3.2, 5.1, 5.2** (~1.5h). That clears every real bug,
makes it deployable, and removes the dead-code smell — the highest signal-per-hour for a
portfolio project.
