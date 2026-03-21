from typing import List
from agentic_ai_interviewer.state import InterviewState
from agentic_ai_interviewer.LLM import get_llm
from agentic_ai_interviewer.tools.vectorstore import load_faiss_index
from langchain_community.tools.tavily_search import TavilySearchResults
from bullmq import Queue
import os

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
db_write_queue = Queue("db_write_queue", {"connection": redis_url})

def extract_jd_skills(state: InterviewState) -> InterviewState:
    session_id = state.get("session_id")
    vectorstore = load_faiss_index(session_id)
    
    # Retrieve JD chunks
    docs = vectorstore.similarity_search("core technical skills required for the job", k=3)
    context = "\n".join([d.page_content for d in docs])
    
    llm = get_llm(temperature=0.1)
    prompt = f"Based on this job description, extract the core technical skills as a comma-separated list:\n{context}"
    
    response = llm.invoke(prompt)
    skills = [s.strip() for s in response.content.split(",")]
    
    return {"jd_skills": skills}

def orchestrator_service(state: InterviewState) -> InterviewState:
    skills = state.get("jd_skills", [])
    if not skills:
        return state
        
    llm = get_llm(temperature=0.3)
    prompt = f"Given these required skills: {skills}, do you need more context about any of them to conduct a deep technical interview? Answer only 'YES' or 'NO'."
    response = llm.invoke(prompt)
    
    needs_search = "YES" in response.content.upper()
    return {"orchestrator_needs_search": needs_search}

def orchestrator_web_search(state: InterviewState) -> InterviewState:
    skills = state.get("jd_skills", [])
    
    tavily = TavilySearchResults()
    search_query = f"Technical interview questions and core concepts for {', '.join(skills[:3])}"
    results = tavily.invoke({"query": search_query})
    
    return {
        "orchestrator_search_results": str(results),
        "orchestrator_needs_search": False # Finished searching
    }

def question_generator(state: InterviewState) -> InterviewState:
    # If question is already ready from refinement loop, skip
    if state.get("question_ready"):
        return state
        
    skills = state.get("jd_skills", [])
    evals = state.get("evaluations", [])
    
    llm = get_llm(temperature=0.7)
    
    context = ""
    if state.get("orchestrator_search_results"):
        context = f"Additional Context: {state.get('orchestrator_search_results')}\n"
        
    history = "\n".join([f"Q: {e['q']} A: {e['a']} Score: {e['score']}" for e in evals])
    
    prompt = f"""
    You are an expert technical interviewer.
    Skills required: {skills}
    Interview History:
    {history}
    {context}
    
    Generate a challenging, specific technical interview question for the candidate. 
    Output ONLY the question text.
    """
    
    response = llm.invoke(prompt)
    draft_q = response.content.strip()
    
    # We formulate a search query to check if we have enough context in DB
    search_query = f"concepts related to: {draft_q}"
    
    return {
        "draft_question": draft_q,
        "search_query": search_query
    }

def check_db_indexes(state: InterviewState) -> InterviewState:
    session_id = state.get("session_id")
    vectorstore = load_faiss_index(session_id)
    
    docs = vectorstore.similarity_search(state.get("search_query", ""), k=2)
    
    # Simple heuristic: if we got confident results, we assume we have context
    # (in a real app, you might use an LLM to evaluate relevance)
    indexes_present = len(docs) > 0 
    
    return {
        "indexes_present_in_db": indexes_present,
        "retrieved_context": [d.page_content for d in docs]
    }

def refine_web_search(state: InterviewState) -> InterviewState:
    tavily = TavilySearchResults()
    results = tavily.invoke({"query": state.get("draft_question", "")})
    
    return {"refinement_search_results": str(results)}

def refine_question(state: InterviewState) -> InterviewState:
    llm = get_llm(temperature=0.3)
    draft = state.get("draft_question", "")
    
    context = state.get("refinement_search_results", "")
    if not context:
        context = "\n".join(state.get("retrieved_context", []))
        
    prompt = f"""
    Draft question: {draft}
    Context: {context}
    
    Refine the draft question to be completely accurate based on the context. 
    Output ONLY the final interview question.
    """
    
    response = llm.invoke(prompt)
    
    return {
        "final_question": response.content.strip(),
        "question_ready": True
    }

def answer_evaluator(state: InterviewState) -> InterviewState:
    llm = get_llm(temperature=0.2)
    q = state.get("final_question", "")
    a = state.get("human_answer", "")
    
    prompt = f"""
    Question: {q}
    Candidate Answer: {a}
    
    Evaluate the answer. Provide a short score (1-10) and brief 1-sentence feedback.
    Format your response EXACTLY like this:
    Score: X
    Feedback: Your feedback here
    """
    
    response = llm.invoke(prompt)
    
    # parse simple response
    lines = response.content.split("\n")
    score = next((l.split(":")[1].strip() for l in lines if "Score" in l), "5")
    feedback = next((l.split(":")[1].strip() for l in lines if "Feedback" in l), "No feedback")
    
    evals = state.get("evaluations", [])
    evals.append({
        "q": q,
        "a": a,
        "score": score,
        "feedback": feedback
    })
    
    current_count = state.get("current_q_count", 0) + 1
    
    return {
        "evaluations": evals,
        "current_q_count": current_count,
        "question_ready": False # Reset for next loop
    }

async def generate_report(state: InterviewState) -> InterviewState:
    llm = get_llm()
    evals = state.get("evaluations", [])
    
    history = "\n".join([f"Q: {e['q']}\nA: {e['a']}\nScore: {e['score']}\nFeedback: {e['feedback']}\n---" for e in evals])
    prompt = f"Synthesize a final candidate report based on this interview:\n{history}"
    
    response = llm.invoke(prompt)
    final_report = response.content.strip()
    
    state["final_report"] = final_report
    
    # Push the completed state to the DB write queue asynchronously
    await db_write_queue.add("save_session", {"state": state})
    
    return {"final_report": final_report}
