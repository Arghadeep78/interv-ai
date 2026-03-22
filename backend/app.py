import os
import sys
import uuid
import json
import time
import io
import asyncio

# Add src to the Python path so we can import our modules smoothly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as redis
from dotenv import load_dotenv

from langgraph.checkpoint.memory import MemorySaver
from agentic_ai_interviewer.graph import build_graph
from agentic_ai_interviewer.LLM import RateLimitExhaustedError
from agentic_ai_interviewer.nodes import generate_report
import pypdf

load_dotenv()

app = FastAPI(
    title="Agentic AI Interviewer",
    description="Real-time AI-powered technical interview system",
    version="2.0.0",
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, restrict to actual frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

# ---------------------------------------------------------------------------
# Lifecycle: Redis + LangGraph compilation
# ---------------------------------------------------------------------------
graph_app = None
redis_client = None


@app.on_event("startup")
async def startup():
    global graph_app, redis_client

    # Redis client for session status tracking
    redis_client = redis.from_url(redis_url, decode_responses=True)

    # Build and compile the graph with Memory checkpointer
    checkpointer = MemorySaver()
    workflow = build_graph()
    graph_app = workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["answer_evaluator"],
    )


@app.on_event("shutdown")
async def shutdown():
    if redis_client:
        await redis_client.close()


# ---------------------------------------------------------------------------
# Helper: Extract text from uploaded files (PDF or plain text)
# ---------------------------------------------------------------------------
async def extract_text_from_upload(file_upload: UploadFile) -> str:
    content = await file_upload.read()
    if file_upload.filename and file_upload.filename.lower().endswith(".pdf"):
        try:
            pdf_reader = pypdf.PdfReader(io.BytesIO(content))
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            return text
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {str(e)}")
    else:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail="Ensure uploaded file is a PDF or UTF-8 text file.",
            )


# ---------------------------------------------------------------------------
# POST /init_interview — Initialize a new interview session
# ---------------------------------------------------------------------------
@app.post("/init_interview")
async def init_interview(
    resume: UploadFile = File(...),
    jd: UploadFile = File(...),
):
    session_id = str(uuid.uuid4())

    # Extract text from uploaded files
    resume_text = await extract_text_from_upload(resume)
    jd_text = await extract_text_from_upload(jd)

    # Set initial session status with 60-minute TTL (3600 seconds)
    await redis_client.set(f"status:{session_id}", "processing", ex=3600)

    # Initial state for the graph
    initial_state = {
        "session_id": session_id,
        "resume_text": resume_text,
        "jd_text": jd_text,
        "start_time": time.time(),
        "evaluations": [],
        "covered_topics": [],
        "jd_topics": [],
        "current_difficulty": "Medium",
        "evaluator_feedback": "",
        "question_ready": False,
        "orchestrator_needs_search": False,
        "orchestrator_search_results": "",
        "draft_question": "",
        "final_question": "",
        "human_answer": "",
        "search_query": "",
        "retrieved_context": [],
        "final_report": "",
        "user_requested_stop": False,
        "requires_hint": False,
        "failed_condition_context": "",
    }

    config = {"configurable": {"thread_id": session_id}}

    # Run the graph asynchronously up to the first interrupt (before answer_evaluator)
    try:
        async for _ in graph_app.astream(initial_state, config):
            pass

        # Mark session as ready (interview has hit the first interrupt)
        await redis_client.set(f"status:{session_id}", "ready", ex=3600)

    except Exception as e:
        await redis_client.set(f"status:{session_id}", f"error: {str(e)}", ex=3600)
        raise HTTPException(status_code=500, detail=f"Graph initialization failed: {str(e)}")

    return {
        "session_id": session_id,
        "status": "ready",
        "message": "Interview initialized. Connect via WebSocket to begin.",
    }


# ---------------------------------------------------------------------------
# GET /status/{session_id} — Check session status
# ---------------------------------------------------------------------------
@app.get("/status/{session_id}")
async def get_status(session_id: str):
    status = await redis_client.get(f"status:{session_id}")
    if not status:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return {"session_id": session_id, "status": status}


# ---------------------------------------------------------------------------
# WebSocket /ws/interview/{session_id} — Real-time interview interaction
# ---------------------------------------------------------------------------
@app.websocket("/ws/interview/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    # Validate session exists and is ready
    status = await redis_client.get(f"status:{session_id}")
    if not status:
        await websocket.close(code=1008, reason="Session not found or expired")
        return

    await websocket.accept()

    config = {"configurable": {"thread_id": session_id}}

    try:
        # 1. Sync state on connect
        current_state = await graph_app.aget_state(config)

        if not current_state or not current_state.values:
            await websocket.send_text(
                json.dumps({"type": "error", "content": "Session state not found."})
            )
            await websocket.close(code=1011)
            return

        # 2. If the graph is paused at the interrupt, stream the question
        if current_state.next and "answer_evaluator" in current_state.next:
            question = current_state.values.get("final_question", "")
            difficulty = current_state.values.get("current_difficulty", "Medium")
            q_number = len(current_state.values.get("evaluations", [])) + 1

            await websocket.send_text(
                json.dumps({
                    "type": "question",
                    "content": question,
                    "difficulty": difficulty,
                    "question_number": q_number,
                })
            )
        else:
            # Graph is still processing (shouldn't normally happen, but handle it)
            await websocket.send_text(
                json.dumps({"type": "status", "content": "processing"})
            )

        # 3. Main interaction loop
        while True:
            # Wait for the candidate's answer
            data = await websocket.receive_text()

            # Notify client that we're processing
            await websocket.send_text(
                json.dumps({"type": "status", "content": "evaluating"})
            )

            # Resume graph with the human answer
            await graph_app.aupdate_state(
                config,
                {"human_answer": data},
                as_node="question_generator",
            )

            # Run the graph until the next interrupt (or END)
            # Wrapped with rate-limit awareness
            try:
                async for _ in graph_app.astream(None, config):
                    pass
            except RateLimitExhaustedError:
                # All 5 retries across all fallback models failed
                # Gracefully conclude the interview with whatever we have
                current_state = await graph_app.aget_state(config)
                evals = current_state.values.get("evaluations", []) if current_state and current_state.values else []
                covered = current_state.values.get("covered_topics", []) if current_state and current_state.values else []
                jd_topics = current_state.values.get("jd_topics", []) if current_state and current_state.values else []

                # Try to generate a report with current data, or use a fallback message
                try:
                    report_state = current_state.values if current_state and current_state.values else {}
                    report_result = await generate_report(report_state)
                    report_text = report_result.get("final_report", "")
                except Exception:
                    report_text = ""

                if not report_text:
                    report_text = (
                        "## Interview Summary\n\n"
                        "The interview was concluded early due to a temporary service limit.\n\n"
                        f"**Questions Completed:** {len(evals)}\n"
                        f"**Topics Covered:** {', '.join(covered) if covered else 'N/A'}\n"
                        f"**Average Score:** {round(sum(e.get('score', 0) for e in evals) / max(len(evals), 1), 1)}/10\n"
                    )

                await websocket.send_text(
                    json.dumps({
                        "type": "report",
                        "content": report_text,
                        "summary": {
                            "total_questions": len(evals),
                            "topics_covered": covered,
                            "topics_required": jd_topics,
                            "average_score": round(
                                sum(e.get("score", 0) for e in evals) / max(len(evals), 1), 1
                            ),
                            "early_termination": True,
                            "reason": "Rate limit exhausted — thanks for the interview!",
                        },
                    })
                )

                await redis_client.set(
                    f"status:{session_id}", "completed_rate_limited", ex=3600
                )
                await websocket.close(code=1000)
                break
            except Exception as e:
                # Check if it's a rate-limit-like transient error (not exhausted)
                err_str = str(e).lower()
                if any(kw in err_str for kw in ("rate limit", "429", "rate_limit", "too many requests")):
                    await websocket.send_text(
                        json.dumps({
                            "type": "rate_limit",
                            "content": "I need a moment to process, please wait...",
                        })
                    )
                    await asyncio.sleep(15)
                    # Retry the stream once after waiting
                    try:
                        async for _ in graph_app.astream(None, config):
                            pass
                    except Exception:
                        # If it fails again, continue the loop and let the client resend
                        await websocket.send_text(
                            json.dumps({
                                "type": "rate_limit",
                                "content": "Still processing... please resend your answer.",
                            })
                        )
                        continue
                else:
                    raise

            # Check the new state
            current_state = await graph_app.aget_state(config)

            if not current_state.next:
                # Graph ended — send the final report
                report = current_state.values.get("final_report", "Interview Complete.")
                evals = current_state.values.get("evaluations", [])
                covered = current_state.values.get("covered_topics", [])
                jd_topics = current_state.values.get("jd_topics", [])

                await websocket.send_text(
                    json.dumps({
                        "type": "report",
                        "content": report,
                        "summary": {
                            "total_questions": len(evals),
                            "topics_covered": covered,
                            "topics_required": jd_topics,
                            "average_score": round(
                                sum(e.get("score", 0) for e in evals) / max(len(evals), 1), 1
                            ),
                        },
                    })
                )

                # Update session status
                await redis_client.set(
                    f"status:{session_id}", "completed", ex=3600
                )
                await websocket.close(code=1000)
                break

            elif "answer_evaluator" in current_state.next:
                # Next question is ready
                question = current_state.values.get("final_question", "")
                difficulty = current_state.values.get("current_difficulty", "Medium")
                evals = current_state.values.get("evaluations", [])
                q_number = len(evals) + 1

                # Send the last evaluation feedback if available
                if evals:
                    last_eval = evals[-1]
                    await websocket.send_text(
                        json.dumps({
                            "type": "evaluation",
                            "score": last_eval.get("score", 0),
                            "feedback": last_eval.get("feedback", ""),
                            "topic_tested": last_eval.get("topic_tested", ""),
                            "difficulty": last_eval.get("difficulty", ""),
                        })
                    )

                # Send the next question
                await websocket.send_text(
                    json.dumps({
                        "type": "question",
                        "content": question,
                        "difficulty": difficulty,
                        "question_number": q_number,
                    })
                )

    except WebSocketDisconnect:
        # Disconnect resilience: the graph state is persisted in Redis.
        # The 40-minute timer continues regardless of connection status.
        # Client can reconnect and resume at any time.
        print(f"Client disconnected from session: {session_id}")
        await redis_client.set(f"status:{session_id}", "disconnected", ex=3600)

    except Exception as e:
        print(f"WebSocket error for session {session_id}: {e}")
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "content": str(e)})
            )
        except Exception:
            pass
