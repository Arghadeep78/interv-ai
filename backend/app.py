import os
import sys
import uuid
import json

# Add src to the Python path so we can import our modules smoothly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, Form, File
from fastapi.middleware.cors import CORSMiddleware
from bullmq import Queue
from pydantic import BaseModel
import redis.asyncio as redis
from dotenv import load_dotenv

from langgraph.checkpoint.memory import MemorySaver 
from agentic_ai_interviewer.graph import build_graph

load_dotenv()

app = FastAPI()

# Add CORS Middleware to allow the frontend to communicate with this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For production, change to the actual frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

parsing_queue = Queue("parsing_queue", {"connection": redis_url})
db_write_queue = Queue("db_write_queue", {"connection": redis_url})
redis_client = redis.from_url(redis_url)

# In-memory checkpointer for demonstration; use LangGraph AsyncRedisSaver for production
memory_checkpointer = MemorySaver()
graph_app = build_graph().compile(
    checkpointer=memory_checkpointer,
    interrupt_before=["answer_evaluator"]
)

@app.post("/init_interview")
async def init_interview(
    resume: UploadFile = File(...),
    jd: UploadFile = File(...)
):
    session_id = str(uuid.uuid4())
    
    # Read files
    resume_text = (await resume.read()).decode("utf-8")
    jd_text = (await jd.read()).decode("utf-8")
    
    # Set initial status
    await redis_client.set(f"status:{session_id}", "processing")
    
    # Push to ingestion worker
    await parsing_queue.add(
        "parse_job", 
        {
            "session_id": session_id,
            "resume_text": resume_text,
            "jd_text": jd_text
        }
    )
    
    return {"session_id": session_id, "status": "processing"}

@app.get("/status/{session_id}")
async def get_status(session_id: str):
    status = await redis_client.get(f"status:{session_id}")
    if not status:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "status": status.decode("utf-8")}

@app.websocket("/ws/interview/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    status_bytes = await redis_client.get(f"status:{session_id}")
    if not status_bytes or status_bytes.decode('utf-8') != "ready":
        await websocket.close(code=1008, reason="Session not ready")
        return
        
    await websocket.accept()
    
    config = {"configurable": {"thread_id": session_id}}
    
    try:
        # 1. Initial graph kick-off (if first connect)
        state_dict = graph_app.get_state(config)
        if not state_dict.next:
            # First run, initialize state
            initial_state = {
                "session_id": session_id,
                "current_q_count": 0,
                "max_q_count": 3,
                "evaluations": [],
                "question_ready": False
            }
            # Stream the generator until interruption
            async for output in graph_app.astream(initial_state, config):
                pass 
                
        # We should now be paused before 'answer_evaluator'
        current_state = graph_app.get_state(config)
        
        if "answer_evaluator" in current_state.next:
            question = current_state.values.get("final_question", "Are you ready?")
            await websocket.send_text(json.dumps({"type": "question", "content": question}))
        
        while True:
            data = await websocket.receive_text()
            
            # Resume graph with human answer
            graph_app.update_state(config, {"human_answer": data}, as_node="answer_evaluator")
            
            # Run the remaining loop until the next interrupt (or END)
            async for output in graph_app.astream(None, config):
                pass
                
            current_state = graph_app.get_state(config)
            
            if not current_state.next:
                # Graph ended, report is generated
                report = current_state.values.get("final_report", "Interview Complete.")
                await websocket.send_text(json.dumps({"type": "report", "content": report}))
                await websocket.close(code=1000)
                break
                
            elif "answer_evaluator" in current_state.next:
                question = current_state.values.get("final_question", "")
                await websocket.send_text(json.dumps({"type": "question", "content": question}))
            
    except WebSocketDisconnect:
        print(f"Client disconnected from session: {session_id}")
