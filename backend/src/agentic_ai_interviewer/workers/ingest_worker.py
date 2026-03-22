import asyncio
import os
import sys
import json
import redis.asyncio as redis
from arq.connections import RedisSettings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# Ensure we can import from the src directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from agentic_ai_interviewer.tools.vectorstore import save_faiss_index
from dotenv import load_dotenv

load_dotenv()

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

async def parse_and_chunk(text: str, source_type: str) -> list[Document]:
    """Simulates parsing a document and chunking it."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = splitter.split_text(text)
    return [Document(page_content=chunk, metadata={"source": source_type}) for chunk in chunks]

async def process_ingestion(ctx, session_id: str, resume_text: str, jd_text: str):
    print(f"Starting ingestion for session: {session_id}")
    
    # Process Resume and JD in parallel
    resume_docs, jd_docs = await asyncio.gather(
        parse_and_chunk(resume_text, "resume"),
        parse_and_chunk(jd_text, "jd")
    )
    
    all_docs = resume_docs + jd_docs
    
    # Embed and save FAISS index locally
    save_faiss_index(all_docs, session_id)
    
    # Update Redis session status
    r = redis.from_url(redis_url)
    await r.set(f"status:{session_id}", "ready")
    await r.close()
    
    print(f"Finished ingestion for session: {session_id}")
    return "Ingestion Complete"

class WorkerSettings:
    functions = [process_ingestion]
    redis_settings = RedisSettings.from_dsn(redis_url)

if __name__ == "__main__":
    from arq.worker import run_worker
    run_worker(WorkerSettings)
