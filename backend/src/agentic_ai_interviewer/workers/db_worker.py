import asyncio
import os
import sys
import json
from arq.connections import RedisSettings
from prisma import Prisma
from dotenv import load_dotenv

# Ensure we can import from the src directory if needed
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

load_dotenv()
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

async def process_db_write(ctx, state: dict):
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
                "extractedSkills": json.dumps(state.get("jd_topics", []))
            }
        })
        print(f"Successfully saved session {session_id} to NeonDB!")
    except Exception as e:
        print(f"Error saving to DB: {e}")
    finally:
        await db.disconnect()

class WorkerSettings:
    functions = [process_db_write]
    redis_settings = RedisSettings.from_dsn(redis_url)

if __name__ == "__main__":
    from arq.worker import run_worker
    run_worker(WorkerSettings)