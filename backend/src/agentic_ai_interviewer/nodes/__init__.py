import time
import json
from typing import List

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_community.tools.tavily_search import TavilySearchResults
from arq import create_pool
from arq.connections import RedisSettings
import os

from agentic_ai_interviewer.state import InterviewState
from agentic_ai_interviewer.LLM import get_llm, invoke_with_retry, RateLimitExhaustedError
from agentic_ai_interviewer.tools.vectorstore import (
    save_faiss_index,
    load_faiss_index,
    add_documents_to_index,
    delete_faiss_index,
    invalidate_faiss_cache,
)

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

_tavily_topic_cache: dict[str, str] = {}  # topic -> search result string


# ---------------------------------------------------------------------------
# 1. INGEST DOCUMENTS
# ---------------------------------------------------------------------------
async def ingest_documents(state: InterviewState) -> dict:
    """
    Takes raw Resume/JD text from the state, chunks them using
    RecursiveCharacterTextSplitter, embeds via HuggingFace, and stores
    in a per-session FAISS index. Records start_time.
    """
    session_id = state["session_id"]
    resume_text = state.get("resume_text", "")
    jd_text = state.get("jd_text", "")

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)

    # Chunk resume
    resume_chunks = splitter.split_text(resume_text)
    resume_docs = [
        Document(page_content=chunk, metadata={"source": "resume"})
        for chunk in resume_chunks
    ]

    # Chunk JD
    jd_chunks = splitter.split_text(jd_text)
    jd_docs = [
        Document(page_content=chunk, metadata={"source": "jd"})
        for chunk in jd_chunks
    ]

    all_docs = resume_docs + jd_docs
    save_faiss_index(all_docs, session_id)

    return {
        "start_time": time.time(),
        "evaluations": [],
        "covered_topics": [],
        "jd_topics": [],
        "current_difficulty": "Easy",
        "evaluator_feedback": "",
        "question_ready": False,
        "router_decision": "default",
    }


# ---------------------------------------------------------------------------
# 2. ORCHESTRATOR SERVICE
# ---------------------------------------------------------------------------
async def orchestrator_service(state: InterviewState) -> dict:
    """
    Queries FAISS to extract JD requirements. Uses Groq to extract a
    comprehensive jd_topics list. If it has search results from Tavily,
    embeds them back into FAISS. Decides if more web context is needed.
    """
    session_id = state["session_id"]
    vectorstore = load_faiss_index(session_id)

    # If we have Tavily search results from a previous search, embed them into FAISS
    search_results = state.get("orchestrator_search_results", "")
    if search_results:
        new_docs = [
            Document(
                page_content=search_results,
                metadata={"source": "tavily_orchestrator"},
            )
        ]
        vectorstore = add_documents_to_index(new_docs, session_id)

    # Retrieve JD chunks for topic extraction (capped at k=3)
    docs = vectorstore.similarity_search(
        "core technical skills and requirements for the job role", k=3
    )
    context = "\n".join([d.page_content[:400] for d in docs])

    # Extract topics if not yet extracted
    existing_topics = state.get("jd_topics", [])
    if not existing_topics:
        extract_prompt = f"""Extract ALL core technical skills and competency areas from this job description to test in an interview.

JD Context:
{context}

Return ONLY a JSON array of topic strings. Example: ["Python", "System Design", "REST APIs"]
Output the JSON array and nothing else."""

        response = await invoke_with_retry(extract_prompt, role="light", temperature=0.1, max_tokens=256)
        try:
            # Try to parse the JSON array from the response
            content = response.content.strip()
            # Handle potential markdown code block wrapping
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            topics = json.loads(content)
            if not isinstance(topics, list):
                topics = [s.strip() for s in content.split(",")]
        except (json.JSONDecodeError, ValueError):
            topics = [s.strip() for s in response.content.split(",")]
    else:
        topics = existing_topics

    # Decide if the LLM needs more context on any of these topics
    decide_prompt = f"""Skills to interview on: {topics}
Available context: {context[:300]}

Do you need a web search for more technical depth on any topic? Answer ONLY 'YES' or 'NO'."""

    decision = await invoke_with_retry(decide_prompt, role="light", temperature=0.1, max_tokens=10)
    needs_search = "YES" in decision.content.upper()

    return {
        "jd_topics": topics,
        "orchestrator_needs_search": needs_search,
    }


# ---------------------------------------------------------------------------
# 3. ORCHESTRATOR WEB SEARCH
# ---------------------------------------------------------------------------
async def orchestrator_web_search(state: InterviewState) -> dict:
    """
    Uses Tavily to find missing context for JD topics the orchestrator
    isn't confident about. Results will be embedded into FAISS on the
    next orchestrator_service pass.
    """
    topics = state.get("jd_topics", [])

    tavily = TavilySearchResults()
    search_query = (
        f"Technical interview deep dive concepts for: {', '.join(topics[:5])}"
    )
    results = await tavily.ainvoke({"query": search_query})

    # Flatten results into a readable string
    if isinstance(results, list):
        summary = "\n".join(
            [r.get("content", str(r)) if isinstance(r, dict) else str(r) for r in results]
        )
    else:
        summary = str(results)

    return {
        "orchestrator_search_results": summary,
        "orchestrator_needs_search": False,  # Done searching
    }





# ---------------------------------------------------------------------------
# 4. QUESTION GENERATOR
# ---------------------------------------------------------------------------
async def question_generator(state: InterviewState) -> dict:
    """
    The 'brain' of the system. Four operational modes (checked in order):

    Mode A – STOP DETECTION (light model)
    Mode B – SOCRATIC HINT
    Mode C – ICEBREAKER (light model, first question only)
    Mode D – NORMAL QUESTION
    """
    session_id = state["session_id"]
    jd_topics = state.get("jd_topics", [])
    covered = state.get("covered_topics", [])
    difficulty = state.get("current_difficulty", "Easy")
    evaluator_feedback = state.get("evaluator_feedback", "")
    evals = state.get("evaluations", [])
    human_answer = state.get("human_answer", "")
    requires_hint = state.get("requires_hint", False)
    failed_condition_context = state.get("failed_condition_context", "")

    # ================================================================
    # MODE A — STOP DETECTION (light model, ~30 tokens prompt)
    # ================================================================
    if human_answer:
        stop_check = await invoke_with_retry(
            f"""Classify: does this candidate response express intent to END/STOP the interview?

Response: \"{human_answer}\"

Output ONLY: STOP or CONTINUE""",
            role="light", temperature=0.0, max_tokens=5,
        )
        if "STOP" in stop_check.content.strip().upper():
            return {
                "user_requested_stop": True,
                "final_question": (
                    "Understood \u2014 thanks for your time today! "
                    "Let me wrap up and generate your evaluation report."
                ),
                "question_ready": True,
            }

    # ================================================================
    # MODE B — SOCRATIC HINT (follow-up on a failed answer)
    # ================================================================
    if requires_hint and failed_condition_context:
        hint_prompt = [
            {"role": "system", "content": """You are a senior interviewer. The candidate gave a wrong answer.
Present a SPECIFIC failing scenario (with numbers/volumes) where their solution breaks.
NEVER reveal the answer. End with a focused question. 2-4 sentences max."""},
            {"role": "user", "content": f"Problem: {failed_condition_context}\nDifficulty: {difficulty}\n\nGenerate the follow-up question only."},
        ]

        response = await invoke_with_retry(
            hint_prompt, role="heavy", temperature=0.7, max_tokens=300,
        )
        follow_up = response.content.strip()

        return {
            "draft_question": follow_up,
            "final_question": follow_up,
            "question_ready": True,
            "requires_hint": False,
            "failed_condition_context": "",
        }

    # ================================================================
    # MODE C — ICEBREAKER (first question, light model)
    # ================================================================
    question_number = len(evals) + 1

    if question_number == 1:
        # Retrieve resume context for personalization
        vectorstore = load_faiss_index(session_id)
        resume_docs = vectorstore.similarity_search(
            "candidate experience projects skills background", k=2
        )
        resume_summary = "\n".join([d.page_content[:300] for d in resume_docs])

        icebreaker_prompt = f"""You are a friendly senior interviewer starting a live interview.
Generate ONE warm, conversational opening question to ease the candidate in.

Pick ONE of these angles:
- "Tell me a bit about yourself and your journey into tech."
- "Walk me through your most recent role and what excited you about it."
- "I see some interesting projects here — what's the one you're most proud of and why?"

Candidate's background: {resume_summary}

Make it natural and encouraging. Output ONLY the question, nothing else."""

        response = await invoke_with_retry(
            icebreaker_prompt, role="light", temperature=0.8, max_tokens=200,
        )
        icebreaker_q = response.content.strip()

        return {
            "draft_question": icebreaker_q,
            "final_question": icebreaker_q,
            "question_ready": True,
            "search_query": "icebreaker",
            "requires_hint": False,
            "failed_condition_context": "",
        }

    # ================================================================
    # MODE D — NORMAL QUESTION GENERATION
    # ================================================================

    # ---- Strict Topic Selection: never re-pick a burned topic ----
    available_topics = [t for t in jd_topics if t not in covered]

    if not available_topics:
        return {
            "user_requested_stop": True,
            "final_question": (
                "We've covered all the key topics for this role — great job "
                "working through everything! Let me compile your evaluation "
                "report now."
            ),
            "question_ready": True,
        }

    next_topic = available_topics[0]

    # ---- FAISS Retrieval (capped k, truncated context) ----
    vectorstore = load_faiss_index(session_id)

    jd_docs = vectorstore.similarity_search(
        f"{next_topic} job requirements skills", k=2
    )
    jd_context = "\n".join([d.page_content[:400] for d in jd_docs if d.metadata.get("source") == "jd"])
    if not jd_context:
        jd_context = "\n".join([d.page_content[:400] for d in jd_docs])

    resume_docs = vectorstore.similarity_search(
        f"{next_topic} project experience built", k=2
    )
    resume_context = "\n".join([d.page_content[:400] for d in resume_docs if d.metadata.get("source") == "resume"])
    if not resume_context:
        resume_context = "\n".join([d.page_content[:400] for d in resume_docs])

    knowledge_docs = vectorstore.similarity_search(
        f"{next_topic} best practices", k=1
    )
    supplementary_context = "\n".join([
        d.page_content[:300] for d in knowledge_docs
        if d.metadata.get("source", "").startswith("tavily")
    ])

    # ---- Sliding-window history (last 5 only) ----
    history = ""
    if evals:
        recent_evals = evals[-5:] if len(evals) > 5 else evals
        history = "Recent History:\n"
        for e in recent_evals:
            history += f"- {e.get('topic_tested', 'N/A')} | Score: {e['score']}/10 | {e.get('difficulty', 'N/A')}\n"

    # ---- Search decision (deterministic) ----
    needs_search = (
        difficulty == "Hard"
        and not supplementary_context  # no cached Tavily docs in FAISS for this topic yet
    )

    # ---- Conditional Tavily Search ----
    tavily_context = ""
    if needs_search:
        try:
            if next_topic in _tavily_topic_cache:
                tavily_context = _tavily_topic_cache[next_topic]
            else:
                tavily = TavilySearchResults()
                tavily_results = await tavily.ainvoke(
                    {"query": f"{difficulty} {next_topic} interview concepts"}
                )
                if isinstance(tavily_results, list):
                    tavily_context = "\n".join(
                        [r.get("content", str(r)) if isinstance(r, dict) else str(r) for r in tavily_results[:2]]
                    )
                _tavily_topic_cache[next_topic] = tavily_context
            if tavily_context:
                tavily_docs = [
                    Document(
                        page_content=tavily_context[:500],
                        metadata={"source": "tavily_question_gen", "topic": next_topic},
                    )
                ]
                add_documents_to_index(tavily_docs, session_id)
        except Exception:
            tavily_context = ""

    # ---- Determine interview phase ----
    if question_number <= 3:
        interview_phase = "Warm-Up"
    elif difficulty == "Hard":
        interview_phase = "Deep-Dive"
    else:
        interview_phase = "Core Assessment"

    is_topic_pivot = evaluator_feedback and any(
        phrase in evaluator_feedback.lower()
        for phrase in ["pivot", "move on", "skip", "next topic"]
    )

    # ---- Condensed system prompt ----
    system_prompt = f"""You are a senior technical interviewer. Be empathetic, conversational, supportive.

ROLE RULES:
- SDE roles: ask architectural, "WHY", system-design questions from candidate's tech stack.
- Non-SDE roles: ask domain-specific analytical/case-study questions.

FOUNDATION FIRST: Start with "why" they chose a technology → probe production concerns → then edge cases (only at Hard difficulty).

RULES:
1. ONE focused {difficulty}-level question testing: {next_topic}
2. NEVER ask to write code/scripts/SQL. Not a coding round.
3. BANNED: "I see you have...", "Based on your CV...", "You mentioned..."
4. Frame questions around real production concerns (scaling, fault tolerance, state sync).
5. Be conversational. Use warm transitions. Don't repeat intros.
{f"6. PREVIOUS FEEDBACK: {evaluator_feedback}" if evaluator_feedback else ""}
{"7. Candidate just pivoted — use a warm transition." if is_topic_pivot else ""}"""

    user_prompt = f"""Topic: {next_topic} | Difficulty: {difficulty} | Q#{question_number} ({interview_phase})

Resume: {resume_context[:400] if resume_context else "(none)"}
JD: {jd_context[:400] if jd_context else "(none)"}
{f"Web research: {tavily_context[:300]}" if tavily_context else ""}
{f"Extra context: {supplementary_context[:200]}" if supplementary_context else ""}
{history}

Generate ONE interview question. Output ONLY the question text."""

    response = await invoke_with_retry(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        role="heavy", temperature=0.7, max_tokens=512,
    )

    final_q = response.content.strip()

    return {
        "draft_question": final_q,
        "final_question": final_q,
        "question_ready": True,
        "search_query": f"{next_topic} {difficulty}",
        "requires_hint": False,
        "failed_condition_context": "",
    }


# ---------------------------------------------------------------------------
# 5. ANSWER EVALUATOR
# ---------------------------------------------------------------------------
async def answer_evaluator(state: InterviewState) -> dict:
    """
    CRITICAL: The graph interrupts BEFORE this node. Once resumed with a
    human_answer, this node scores the answer, determines topic_tested,
    appends to covered_topics, adjusts current_difficulty adaptively,
    and generates evaluator_feedback for the next question cycle.

    TOPIC BURN PROTOCOL:
        A topic is "burned" (added to covered_topics, moving to next) if:
        1. The LLM detects skip intent ("I don't know", "pass", "skip"), OR
        2. The candidate fails a Socratic hint (score<=5 while requires_hint
           was already True — the "Max 1 Hint" rule).
        When burned, difficulty is FROZEN (no change).

    SOCRATIC FAILURE PROTOCOL (Max 1 Hint):
        On FIRST failure (score<=5, not already hinting), sets
        requires_hint=True for one Socratic follow-up. If the follow-up
        also fails, the topic is burned.

    GRADUAL DIFFICULTY:
        Difficulty changes by at most +1 level per turn. Hard is only
        unlocked after a Medium-level score >= 8.
    """
    session_id = state["session_id"]
    question = state.get("final_question", "")
    answer = state.get("human_answer", "")
    difficulty = state.get("current_difficulty", "Medium")
    jd_topics = state.get("jd_topics", [])
    covered = list(state.get("covered_topics", []))

    # Retrieve context from FAISS for fact-checking (capped and truncated)
    vectorstore = load_faiss_index(session_id)
    fact_docs = vectorstore.similarity_search(
        f"{question} answer verification", k=2
    )
    fact_context = "\n".join([d.page_content[:400] for d in fact_docs])

    # Tavily fact-checking (Hard questions only, when no useful local context)
    should_fact_check = difficulty == "Hard" and not fact_context.strip()
    tavily_context = ""
    if should_fact_check:
        try:
            cache_key = question[:80]
            if cache_key in _tavily_topic_cache:
                tavily_context = _tavily_topic_cache[cache_key]
            else:
                tavily = TavilySearchResults()
                tavily_results = await tavily.ainvoke(
                    {"query": f"Verify: {question[:80]} correct answer"}
                )
                if isinstance(tavily_results, list):
                    tavily_context = "\n".join(
                        [r.get("content", str(r)) if isinstance(r, dict) else str(r) for r in tavily_results[:1]]
                    )
                _tavily_topic_cache[cache_key] = tavily_context
        except Exception:
            tavily_context = ""

    eval_prompt = f"""Evaluate this interview answer. Respond in JSON only.

Question ({difficulty}): {question}
Answer: {answer}
Reference: {fact_context[:500]}
{f"Verification: {tavily_context[:300]}" if tavily_context else ""}
JD Topics: {jd_topics}

JSON format:
{{
    "score": <1-10>,
    "topic_tested": "<topic from JD topics>",
    "is_skip_intent": <true if candidate wants to skip ("I don't know", "pass", "skip"), false if they attempted an answer>,
    "feedback": "<2-3 sentence evaluation>",
    "evaluator_feedback": "<guidance for next question>",
    "requires_hint": <true if score<=5 AND not skipping AND attempted a real answer>,
    "failed_condition_context": "<specific failing scenario if requires_hint is true, else empty>"
}}

Scoring: 1-3=wrong, 4-5=partial, 6-7=good, 8-9=excellent, 10=perfect.
Output ONLY the JSON."""

    response = await invoke_with_retry(eval_prompt, role="heavy", temperature=0.2, max_tokens=512)

    # Parse the evaluation response
    try:
        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        eval_data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        # Fallback parsing
        eval_data = {
            "score": 5,
            "topic_tested": jd_topics[0] if jd_topics else "general",
            "is_skip_intent": False,
            "feedback": response.content.strip(),
            "evaluator_feedback": "Continue with the next topic.",
            "requires_hint": False,
            "failed_condition_context": "",
        }

    score = int(eval_data.get("score", 5))
    topic_tested = eval_data.get("topic_tested", "general")
    is_skip_intent = bool(eval_data.get("is_skip_intent", False))
    feedback = eval_data.get("feedback", "No feedback")
    next_feedback = eval_data.get("evaluator_feedback", "")
    hint_needed = bool(eval_data.get("requires_hint", False))
    failed_ctx = eval_data.get("failed_condition_context", "")

    # Was the candidate already responding to a Socratic hint?
    was_on_hint = state.get("requires_hint", False)

    # ---- TOPIC BURN DETECTION ----
    # Burn condition 1: candidate explicitly wants to skip
    # Burn condition 2: candidate failed the Socratic follow-up (Max 1 Hint rule)
    failed_hint = was_on_hint and score <= 5
    topic_burned = is_skip_intent or failed_hint

    if topic_burned:
        # ---- TOPIC BURN: freeze difficulty, mark topic as covered, move on ----
        if topic_tested not in covered:
            covered.append(topic_tested)

        # Conversational pivot feedback for the question generator
        if is_skip_intent:
            next_feedback = (
                "The candidate chose to skip this topic. Let's pivot to "
                "something else — start with a warm, encouraging transition."
            )
        else:
            next_feedback = (
                "The candidate attempted a follow-up hint but didn't get there. "
                "No worries — let's move on to the next topic with an encouraging tone."
            )

        # Append evaluation record
        evals = list(state.get("evaluations", []))
        evals.append({
            "q": question,
            "a": answer,
            "score": score,
            "difficulty": difficulty,
            "topic_tested": topic_tested,
            "feedback": feedback,
            "skipped": is_skip_intent,
            "burned": True,
        })

        return {
            "evaluations": evals,
            "covered_topics": covered,
            "current_difficulty": difficulty,   # FROZEN — no change
            "evaluator_feedback": next_feedback,
            "question_ready": False,
            "requires_hint": False,              # clear hint state
            "failed_condition_context": "",       # clear
        }

    # ---- NORMAL FLOW (not burned) ----

    # Mark topic as covered if the candidate passed (no hint needed)
    if not hint_needed and topic_tested not in covered:
        covered.append(topic_tested)

    # ---- Gradual Difficulty Adjustment (+1 max, Medium-pass gate) ----
    DIFFICULTY_LEVELS = ["Easy", "Medium", "Hard"]
    current_idx = DIFFICULTY_LEVELS.index(difficulty) if difficulty in DIFFICULTY_LEVELS else 1
    new_difficulty = difficulty

    # Append evaluation record BEFORE difficulty check (so current eval is included)
    evals = list(state.get("evaluations", []))
    evals.append({
        "q": question,
        "a": answer,
        "score": score,
        "difficulty": difficulty,
        "topic_tested": topic_tested,
        "feedback": feedback,
    })

    if score <= 3:
        # Failed badly — scale DOWN by 1 level
        new_difficulty = DIFFICULTY_LEVELS[max(0, current_idx - 1)]
    elif score >= 8:
        # Aced it — scale UP by 1 level (with Medium-pass gate for Hard)
        candidate_idx = min(current_idx + 1, len(DIFFICULTY_LEVELS) - 1)
        if DIFFICULTY_LEVELS[candidate_idx] == "Hard":
            # Hard is only unlocked if candidate has passed a Medium question with >= 8
            has_medium_pass = any(
                e.get("difficulty") == "Medium" and int(e.get("score", 0)) >= 8
                for e in evals
            )
            if not has_medium_pass:
                candidate_idx = 1  # stay at Medium
        new_difficulty = DIFFICULTY_LEVELS[candidate_idx]
    # Scores 4-7: maintain current difficulty

    return {
        "evaluations": evals,
        "covered_topics": covered,
        "current_difficulty": new_difficulty,
        "evaluator_feedback": next_feedback,
        "question_ready": False,  # Reset for next loop
        "requires_hint": hint_needed,
        "failed_condition_context": failed_ctx,
    }


# ---------------------------------------------------------------------------
# 6. CONFIDENCE ROUTER
# ---------------------------------------------------------------------------
async def confidence_router(state: InterviewState) -> dict:
    """
    Signal-driven routing node that sits between answer_evaluator and
    generate_appreciation. Reads the last evaluation score and makes three
    decisions:

    1. NEEDS_AUGMENTATION — Hard question, score < 6, no cached Tavily content
       for the topic in FAISS yet → fetch and cache web context NOW so the next
       question on this topic is grounded. Sets router_decision="augmented".

    2. TOPIC_MASTERED — score >= 8 → re-rank available_topics by JD embedding
       similarity so the most relevant uncovered topic is asked next.
       Sets router_decision="reranked".

    3. DEFAULT — anything else → no-op, lets normal flow continue.
       Sets router_decision="default".

    The node never changes covered_topics or difficulty — that is the
    evaluator's job. It only enriches context or reorders the topic queue
    so question_generator gets better inputs.
    """
    session_id = state["session_id"]
    evals = state.get("evaluations", [])
    jd_topics = state.get("jd_topics", [])
    covered = state.get("covered_topics", [])

    if not evals:
        return {"router_decision": "default"}

    last_eval = evals[-1]
    score = int(last_eval.get("score", 5))
    topic_tested = last_eval.get("topic_tested", "")
    difficulty = state.get("current_difficulty", "Medium")

    # ------------------------------------------------------------------ #
    # PATH 1 — LOW-SCORE AUGMENTATION                                     #
    # Fetch targeted Tavily context for the failing topic so the next     #
    # question (Socratic or otherwise) has fresh grounding.               #
    # ------------------------------------------------------------------ #
    if score < 6 and difficulty == "Hard" and topic_tested:
        cache_key = f"router_{topic_tested}"
        if cache_key not in _tavily_topic_cache:
            try:
                tavily = TavilySearchResults()
                results = await tavily.ainvoke(
                    {"query": f"{topic_tested} common misconceptions correct explanation"}
                )
                if isinstance(results, list):
                    augment_text = "\n".join(
                        [r.get("content", str(r)) if isinstance(r, dict) else str(r)
                         for r in results[:2]]
                    )
                else:
                    augment_text = str(results)

                _tavily_topic_cache[cache_key] = augment_text

                if augment_text:
                    add_documents_to_index(
                        [Document(
                            page_content=augment_text[:600],
                            metadata={"source": "tavily_router", "topic": topic_tested},
                        )],
                        session_id,
                    )
            except Exception:
                pass

        return {"router_decision": "augmented"}

    # ------------------------------------------------------------------ #
    # PATH 2 — TOPIC MASTERY RERANK                                       #
    # Candidate aced the last question. Re-rank remaining topics by       #
    # cosine similarity to the JD embedding so the most relevant gap      #
    # is addressed next rather than blindly following list order.         #
    # ------------------------------------------------------------------ #
    if score >= 8:
        available = [t for t in jd_topics if t not in covered]
        if len(available) > 1:
            try:
                vectorstore = load_faiss_index(session_id)
                scored_topics: list[tuple[float, str]] = []
                for topic in available:
                    docs = vectorstore.similarity_search_with_score(
                        f"{topic} job requirements skills", k=1
                    )
                    # FAISS returns L2 distance — lower is more similar.
                    # Convert to a similarity rank by negating.
                    sim = -docs[0][1] if docs else -999.0
                    scored_topics.append((sim, topic))

                scored_topics.sort(reverse=True)
                reranked = [t for _, t in scored_topics]

                # Rebuild jd_topics with covered topics preserved at front,
                # reranked available topics appended after.
                new_order = [t for t in jd_topics if t in covered] + reranked
                return {"jd_topics": new_order, "router_decision": "reranked"}
            except Exception:
                pass

    return {"router_decision": "default"}


# ---------------------------------------------------------------------------
# 6. GENERATE APPRECIATION
# ---------------------------------------------------------------------------
async def generate_appreciation(state: InterviewState) -> dict:
    """
    Acts as a Supportive Human Interviewer. Generates a short, empathetic appreciation
    or encouraging transition based on the candidate's last answer score before moving
    to the next technical question.
    """
    evals = state.get("evaluations", [])
    if not evals:
        return {"appreciation_text": ""}

    last_eval = evals[-1]
    score = last_eval.get("score", 5)
    candidate_answer = last_eval.get("a", "")

    prompt = f"""You are the empathetic and encouraging persona of an expert technical interviewer. Your job is to make the candidate feel valued and comfortable.
Score: {score}/10
Candidate's Answer: {candidate_answer}
Task:
- If the score is 7 or above: Generate a brief, warm, and highly natural appreciation (1-2 sentences max). Sound like a real human (e.g., 'Spot on', 'Great explanation').
- If the score is below 7, or if the candidate struggled/skipped: Provide a very brief, gentle, and encouraging transition (e.g., 'No worries, that is a tough one', 'Good effort, let us pivot').
Constraints: DO NOT ask questions. DO NOT explain concepts. Output ONLY the conversational text."""

    try:
        response = await invoke_with_retry(prompt, role="light", temperature=0.7, max_tokens=100)
        appreciation_text = response.content.strip()
    except RateLimitExhaustedError:
        appreciation_text = ""
        
    return {"appreciation_text": appreciation_text}


# ---------------------------------------------------------------------------
# 7. GENERATE REPORT
# ---------------------------------------------------------------------------
async def generate_report(state: InterviewState) -> dict:
    """
    Synthesizes all evaluations into a final structured markdown report
    with scores, topic coverage analysis, and recommendations.
    """
    evals = state.get("evaluations", [])
    jd_topics = state.get("jd_topics", [])
    covered = state.get("covered_topics", [])
    start_time = state.get("start_time", 0)
    duration_mins = round((time.time() - start_time) / 60, 1) if start_time else 0

    llm = get_llm(role="heavy", temperature=0.3, max_tokens=1500)

    # Build evaluation summary
    eval_summary = ""
    for i, e in enumerate(evals, 1):
        eval_summary += f"""
### Question {i} — {e.get('topic_tested', 'N/A')} (Difficulty: {e.get('difficulty', 'N/A')})
**Q:** {e['q']}
**A:** {e['a']}
**Score:** {e['score']}/10
**Feedback:** {e.get('feedback', 'N/A')}
---"""

    uncovered = [t for t in jd_topics if t not in covered]
    avg_score = round(sum(e.get("score", 0) for e in evals) / max(len(evals), 1), 1)

    prompt = f"""You are writing a comprehensive candidate evaluation report after a technical interview.

Interview Duration: {duration_mins} minutes
Total Questions Asked: {len(evals)}
Average Score: {avg_score}/10
Topics Covered: {covered}
Topics NOT Covered: {uncovered}
JD Required Topics: {jd_topics}

Detailed Evaluation:
{eval_summary}

Write a professional, structured markdown report that includes:
1. **Executive Summary** — overall candidate assessment (2-3 sentences)
2. **Score Breakdown** — a table with Topic, Difficulty, Score, and Key Observations
3. **Strengths** — areas where the candidate excelled
4. **Areas for Improvement** — gaps and weaknesses identified  
5. **Topic Coverage Analysis** — what was tested vs. what remains untested
6. **Recommendation** — Hire / Needs Further Evaluation / Do Not Hire with justification
7. **Suggested Follow-up Questions** — for uncovered topics

Make the report actionable and useful for hiring managers."""

    llm_response = await invoke_with_retry(prompt, role="heavy", temperature=0.3, max_tokens=1500)
    final_report = llm_response.content.strip()

    # Persist to DB via ARQ worker
    try:
        redis_settings = RedisSettings.from_dsn(redis_url)
        pool = await create_pool(redis_settings)
        await pool.enqueue_job(
            "process_db_write",
            {
                "session_id": state["session_id"],
                "final_report": final_report,
                "evaluations": evals,
                "jd_topics": jd_topics,
            },
        )
        await pool.close()
    except Exception as e:
        print(f"Failed to enqueue DB write job: {e}")

    # The interview is over — drop this session's FAISS index so completed
    # sessions don't leak disk, and evict the in-process cache entry so it
    # doesn't grow unbounded across sessions. Best-effort: stale state is harmless.
    try:
        delete_faiss_index(state["session_id"])
        invalidate_faiss_cache(state["session_id"])
    except Exception as e:
        print(f"Failed to delete FAISS index for session {state.get('session_id')}: {e}")

    return {"final_report": final_report}
