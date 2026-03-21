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
from agentic_ai_interviewer.LLM import get_llm
from agentic_ai_interviewer.tools.vectorstore import (
    save_faiss_index,
    load_faiss_index,
    add_documents_to_index,
)

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")


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
        "current_difficulty": "Medium",
        "evaluator_feedback": "",
        "question_ready": False,
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

    # Retrieve JD chunks for topic extraction
    docs = vectorstore.similarity_search(
        "core technical skills and requirements for the job role", k=5
    )
    context = "\n".join([d.page_content for d in docs])

    llm = get_llm(temperature=0.1)

    # Extract topics if not yet extracted
    existing_topics = state.get("jd_topics", [])
    if not existing_topics:
        extract_prompt = f"""Based on this job description context, extract ALL core technical skills, 
technologies, and competency areas that should be tested in an interview.

Job Description Context:
{context}

Return ONLY a JSON array of topic strings. Example: ["Python", "System Design", "REST APIs", "SQL"]
Output the JSON array and nothing else."""

        response = await llm.ainvoke(extract_prompt)
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
    decide_prompt = f"""You are preparing to interview a candidate on these skills: {topics}

Available context from the job description and resume:
{context}

Do you have sufficient technical depth on ALL of these topics to ask expert-level 
interview questions? If any topic is too niche or unfamiliar, answer YES to indicate 
you need a web search for more context. Otherwise answer NO.

Answer ONLY 'YES' or 'NO'."""

    decision = await llm.ainvoke(decide_prompt)
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
    The 'brain' of the system. Picks the next untested topic, performs
    separate FAISS retrievals for JD context and resume projects/experience,
    lets the LLM decide if a Tavily web search would improve the question,
    and generates a contextual interview question that references the
    candidate's real projects and work experience when relevant.
    """
    session_id = state["session_id"]
    jd_topics = state.get("jd_topics", [])
    covered = state.get("covered_topics", [])
    difficulty = state.get("current_difficulty", "Medium")
    evaluator_feedback = state.get("evaluator_feedback", "")
    evals = state.get("evaluations", [])

    # Pick the next untested topic
    remaining = [t for t in jd_topics if t not in covered]
    if not remaining:
        # All topics covered — pick the weakest one for re-assessment
        if evals:
            weakest = min(evals, key=lambda e: int(e.get("score", 5)))
            next_topic = weakest.get("topic_tested", jd_topics[0] if jd_topics else "general")
        else:
            next_topic = jd_topics[0] if jd_topics else "general programming"
    else:
        next_topic = remaining[0]

    # ---- FAISS Retrieval: Separate queries for JD and Resume context ----
    vectorstore = load_faiss_index(session_id)

    # Query 1: JD-specific context for the topic (what the role demands)
    jd_docs = vectorstore.similarity_search(
        f"{next_topic} job requirements skills responsibilities", k=3
    )
    jd_context = "\n".join([d.page_content for d in jd_docs if d.metadata.get("source") == "jd"])
    # Fallback: if no JD-tagged docs, use all results
    if not jd_context:
        jd_context = "\n".join([d.page_content for d in jd_docs])

    # Query 2: Resume-specific context (candidate's projects, experience, skills)
    resume_docs = vectorstore.similarity_search(
        f"{next_topic} project experience work built implemented designed", k=4
    )
    resume_context = "\n".join([d.page_content for d in resume_docs if d.metadata.get("source") == "resume"])
    if not resume_context:
        resume_context = "\n".join([d.page_content for d in resume_docs])

    # Query 3: Any previously embedded Tavily/orchestrator knowledge
    knowledge_docs = vectorstore.similarity_search(
        f"{next_topic} advanced concepts best practices", k=2
    )
    supplementary_context = "\n".join([
        d.page_content for d in knowledge_docs
        if d.metadata.get("source", "").startswith("tavily")
    ])

    # Build interview history summary
    history = ""
    if evals:
        history = "Previous Interview History:\n"
        for e in evals:
            history += (
                f"- Topic: {e.get('topic_tested', 'N/A')} | "
                f"Q: {e['q'][:80]}... | Score: {e['score']}/10 | "
                f"Difficulty: {e.get('difficulty', 'N/A')}\n"
            )

    # ---- LLM Decision: Does the question need a Tavily web search? ----
    llm = get_llm(temperature=0.3)

    decide_prompt = f"""You are preparing a {difficulty}-level interview question about "{next_topic}".

Context available from the candidate's resume:
{resume_context[:500] if resume_context else "(No resume context found for this topic)"}

Context available from the job description:
{jd_context[:500] if jd_context else "(No JD context found for this topic)"}

{f"Supplementary knowledge: {supplementary_context[:300]}" if supplementary_context else ""}

Do you have ENOUGH context to ask a high-quality, technically accurate {difficulty} question about {next_topic}?
If the topic is niche or you need current best practices / specific technical details to frame a strong question, answer SEARCH.
If you already have sufficient context, answer READY.

Answer ONLY 'SEARCH' or 'READY'."""

    decision = await llm.ainvoke(decide_prompt)
    needs_search = "SEARCH" in decision.content.upper()

    # ---- Conditional Tavily Search ----
    tavily_context = ""
    if needs_search:
        try:
            tavily = TavilySearchResults()
            tavily_results = await tavily.ainvoke(
                {"query": f"Expert {difficulty} level {next_topic} interview question concepts best practices"}
            )
            if isinstance(tavily_results, list):
                tavily_context = "\n".join(
                    [r.get("content", str(r)) if isinstance(r, dict) else str(r) for r in tavily_results[:3]]
                )
                # Embed Tavily results back into FAISS for future retrieval
                if tavily_context:
                    tavily_docs = [
                        Document(
                            page_content=tavily_context,
                            metadata={"source": "tavily_question_gen", "topic": next_topic},
                        )
                    ]
                    add_documents_to_index(tavily_docs, session_id)
        except Exception:
            tavily_context = ""

    # ---- Generate the question ----
    llm = get_llm(temperature=0.7)

    system_prompt = f"""You are a REAL technical interviewer conducting a live interview. 
You are NOT an AI assistant — you are a senior engineer evaluating a candidate.

RULES:
- Ask ONE specific, focused technical question at a time
- The question MUST be at {difficulty} difficulty level
- The question MUST test the topic: {next_topic}
- Frame questions that require practical knowledge, not just textbook definitions
- IMPORTANT: When the candidate's resume mentions projects, work experience, or 
  technologies relevant to the topic, REFERENCE THEM DIRECTLY in your question.
  For example: "I see you worked on [project name] — can you walk me through how you..."
  or "Your resume mentions experience with [tech] at [company] — how did you handle..."
- This makes the interview feel personal and tests whether they truly did what they claim
- For Easy: test fundamental understanding and basic application
- For Medium: test deeper understanding, trade-offs, and practical scenarios
- For Hard: test advanced concepts, edge cases, system design, and optimization
- Be conversational and natural, like a real interviewer would be
- Do NOT reveal that you are an AI or mention any scoring system

{f"FEEDBACK FROM PREVIOUS ANSWER: {evaluator_feedback}" if evaluator_feedback else ""}
{f"Use this feedback to calibrate the depth and angle of your next question." if evaluator_feedback else ""}"""

    user_prompt = f"""Topic to test: {next_topic}
Difficulty: {difficulty}

=== CANDIDATE'S RESUME (Projects, Experience, Skills) ===
{resume_context if resume_context else "(No specific resume context found for this topic)"}

=== JOB DESCRIPTION REQUIREMENTS ===
{jd_context if jd_context else "(No specific JD context found for this topic)"}

{f"=== WEB RESEARCH (Technical Depth) ==={chr(10)}{tavily_context}" if tavily_context else ""}

{f"=== SUPPLEMENTARY KNOWLEDGE ==={chr(10)}{supplementary_context}" if supplementary_context else ""}

{history}

INSTRUCTION: If the resume mentions specific projects, tools, or experience relevant 
to "{next_topic}", frame your question around those real experiences. Otherwise, ask 
a scenario-based question grounded in the JD requirements.

Generate your interview question now. Output ONLY the question text, nothing else."""

    response = await llm.ainvoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])

    final_q = response.content.strip()

    return {
        "draft_question": final_q,
        "final_question": final_q,
        "question_ready": True,
        "search_query": f"{next_topic} {difficulty}",
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
    """
    session_id = state["session_id"]
    question = state.get("final_question", "")
    answer = state.get("human_answer", "")
    difficulty = state.get("current_difficulty", "Medium")
    jd_topics = state.get("jd_topics", [])
    covered = list(state.get("covered_topics", []))

    # Retrieve context from FAISS for fact-checking
    vectorstore = load_faiss_index(session_id)
    fact_docs = vectorstore.similarity_search(
        f"{question} answer verification", k=3
    )
    fact_context = "\n".join([d.page_content for d in fact_docs])

    # Use Tavily for additional fact-checking if the answer involves specific claims
    tavily_context = ""
    try:
        tavily = TavilySearchResults()
        tavily_results = await tavily.ainvoke(
            {"query": f"Verify: {question[:100]} correct answer"}
        )
        if isinstance(tavily_results, list):
            tavily_context = "\n".join(
                [r.get("content", str(r)) if isinstance(r, dict) else str(r) for r in tavily_results[:2]]
            )
    except Exception:
        tavily_context = ""

    llm = get_llm(temperature=0.2)

    eval_prompt = f"""You are an expert technical interviewer evaluating a candidate's answer.

Question Asked (Difficulty: {difficulty}):
{question}

Candidate's Answer:
{answer}

Reference Context (from resume/JD/knowledge base):
{fact_context}

{f"Additional Verification Context: {tavily_context}" if tavily_context else ""}

Available JD Topics: {jd_topics}

Evaluate the answer and respond in EXACTLY this JSON format:
{{
    "score": <integer 1-10>,
    "topic_tested": "<the primary technical topic this question tested — must be from the JD topics list if possible>",
    "feedback": "<detailed 2-3 sentence evaluation of the answer's quality>",
    "evaluator_feedback": "<specific guidance for the NEXT question: what to probe deeper on, what gaps were revealed, what to adjust>"
}}

Scoring guide:
- 1-3: Incorrect or shows fundamental misunderstanding
- 4-5: Partially correct but lacks depth
- 6-7: Good understanding with minor gaps
- 8-9: Excellent, demonstrates deep practical knowledge
- 10: Perfect, expert-level answer

Output ONLY the JSON object, nothing else."""

    response = await llm.ainvoke(eval_prompt)

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
            "feedback": response.content.strip(),
            "evaluator_feedback": "Continue with the next topic.",
        }

    score = int(eval_data.get("score", 5))
    topic_tested = eval_data.get("topic_tested", "general")
    feedback = eval_data.get("feedback", "No feedback")
    next_feedback = eval_data.get("evaluator_feedback", "")

    # Add topic to covered list
    if topic_tested not in covered:
        covered.append(topic_tested)

    # Adaptive difficulty adjustment
    new_difficulty = difficulty
    if score <= 3:
        # Failed badly — scale down
        if difficulty == "Hard":
            new_difficulty = "Medium"
        elif difficulty == "Medium":
            new_difficulty = "Easy"
    elif score >= 8:
        # Aced it — scale up
        if difficulty == "Easy":
            new_difficulty = "Medium"
        elif difficulty == "Medium":
            new_difficulty = "Hard"
    # Scores 4-7: maintain current difficulty

    # Append evaluation record
    evals = list(state.get("evaluations", []))
    evals.append({
        "q": question,
        "a": answer,
        "score": score,
        "difficulty": difficulty,
        "topic_tested": topic_tested,
        "feedback": feedback,
    })

    return {
        "evaluations": evals,
        "covered_topics": covered,
        "current_difficulty": new_difficulty,
        "evaluator_feedback": next_feedback,
        "question_ready": False,  # Reset for next loop
    }


# ---------------------------------------------------------------------------
# 6. GENERATE REPORT
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

    llm = get_llm(temperature=0.3)

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

    response = await llm.ainvoke(prompt)
    final_report = response.content.strip()

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

    return {"final_report": final_report}
