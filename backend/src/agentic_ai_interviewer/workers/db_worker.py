import asyncio
import os
import sys
import json
from bullmq import Worker, Job
from prisma import Prisma
from dotenv import load_dotenv

# Ensure we can import from the src directory if needed
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

load_dotenv()
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

async def process_db_write(job: Job):
    state = job.data.get("state", {})
    session_id = state.get("session_id")
    
    print(f"Writing session {session_id} to DB...")
    
    db = Prisma()
    await db.connect()
    
    try:
        await db.interviewsession.create({
            "data": {
                "sessionId": session_id,
                "finalReport": state.get("final_report", ""),
                "evaluations": json.dumps(state.get("evaluations", [])),
                "extractedSkills": json.dumps(state.get("jd_skills", []))
            }
        })
        print(f"Successfully saved session {session_id} to NeonDB!")
    except Exception as e:
        print(f"Error saving to DB: {e}")
    finally:
        await db.disconnect()

async def start_db_worker():
    print("Starting DB Worker...")
    worker = Worker(
        "db_write_queue",
        process_db_write,
        {"connection": redis_url}
    )
    # Wait indefinitely
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(start_db_worker())