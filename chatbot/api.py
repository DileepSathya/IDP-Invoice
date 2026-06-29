"""
FastAPI backend for the Invoice Chatbot.

Endpoints:
  POST /chat   — send a message, get a response
  GET  /health — confirm the service is up
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pipeline import chat

app = FastAPI(title="Invoice Chatbot API", version="1.0.0")

# Allow requests from the Streamlit UI (localhost) and any other origin during dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str       # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    debug: bool = False         # if True, include intent + raw result in response


class ChatResponse(BaseModel):
    answer: str
    intent: dict = {}
    raw: dict = {}
    error: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest):
    history = [{"role": m.role, "content": m.content} for m in req.history]
    result  = chat(req.message, history)

    if not req.debug:
        result["intent"] = {}
        result["raw"]    = {}

    return result


# ── Run directly ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
