import os
import sys
import uuid
import json
import io

# Add src to the Python path so we can import our modules smoothly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, Form, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import redis.asyncio as redis
from arq import create_pool
from arq.connections import RedisSettings
from dotenv import load_dotenv

from langgraph.checkpoint.memory import MemorySaver 
from agentic_ai_interviewer.graph import build_graph
import pypdf

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
redis_settings = RedisSettings.from_dsn(redis_url)

# Setup ARQ Redis Pool
@app.on_event("startup")
async def startup():
    app.state.redis_pool = await create_pool(redis_settings)
    app.state.redis_client = redis.from_url(redis_url)

@app.on_event("shutdown")
async def shutdown():
    await app.state.redis_client.close()

# In-memory checkpointer for demonstration; use LangGraph AsyncRedisSaver for production
memory_checkpointer = MemorySaver()
graph_app = build_graph().compile(
    checkpointer=memory_checkpointer,
    interrupt_before=["answer_evaluator"]
)

async def extract_text_from_upload(file_upload: UploadFile) -> str:
    content = await file_upload.read()
    if file_upload.filename.lower().endswith('.pdf'):
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
             # Fallback if not utf-8 text and not a pdf extension
             raise HTTPException(status_code=400, detail="Ensure uploaded file is an answering PDF or UTF-8 text file.")

@app.post("/init_interview")
async def init_interview(
    resume: UploadFile = File(...),
    jd: UploadFile = File(...)
):
    session_id = str(uuid.uuid4())
    
    # Read files handling both text and pdfs
    resume_text = await extract_text_from_upload(resume)
    jd_text = await extract_text_from_upload(jd)
    
    # Set initial status
    await app.state.redis_client.set(f"status:{session_id}", "processing")
    
    # Push to ingestion worker via ARQ
    await app.state.redis_pool.enqueue_job(
        "process_ingestion",
        session_id=session_id,
        resume_text=resume_text,
        jd_text=jd_text
    )
    
    return {"session_id": session_id, "status": "processing"}

@app.get("/status/{session_id}")
async def get_status(session_id: str):
    status = await app.state.redis_client.get(f"status:{session_id}")
    if not status:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "status": status.decode("utf-8")}

@app.websocket("/ws/interview/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    status_bytes = await app.state.redis_client.get(f"status:{session_id}")
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
