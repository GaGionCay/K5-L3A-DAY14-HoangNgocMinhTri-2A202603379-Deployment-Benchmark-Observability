"""
⚠️ EDUCATIONAL ANTI-PATTERN EXAMPLE: Localhost-only Agent

DO NOT USE IN PRODUCTION.
This file demonstrates common development anti-patterns for educational contrast:
- Hardcoded configuration without environment variable management
- Missing health and readiness checks (/health, /ready)
- Binding to localhost rather than 0.0.0.0
- Hardcoded port binding ignoring platform PORT environment variables
- Debug reload active in server runner

See 01-localhost-vs-production/production/app.py for the 12-factor production implementation.
"""
import logging

from fastapi import FastAPI
import uvicorn
from utils.mock_llm import ask

app = FastAPI(title="My Agent")
logger = logging.getLogger(__name__)

# ❌ Vấn đề 2: Không có config management
DEBUG = True
MAX_TOKENS = 500


@app.get("/")
def home():
    return {"message": "Hello! Agent is running on my machine :)"}

# curl -X POST "http://localhost:8000/ask?question=Hello"
@app.post("/ask")
def ask_agent(question: str):
    # Log only non-sensitive request metadata; never log question contents or credentials.
    response = ask(question)
    logger.info("Request completed", extra={"question_length": len(question)})
    return {"answer": response}


# ❌ Vấn đề 4: Không có health check endpoint
# Nếu agent crash, platform không biết để restart

# ❌ Vấn đề 5: Port cố định — không đọc từ environment
# Trên Railway/Render, PORT được inject qua env var
if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="localhost",   # ❌ chỉ chạy được trên local
        port=8000,          # ❌ cứng port
        reload=True         # ❌ debug reload trong production
    )
