from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from mcl.router import route
from cognition.phi.phi_hook import analyze_text

from agents.memory.memory_orchestrator import run_once as build_context_packet
from config import runtime_dir
from agents.memory.memory_loader import read_json, write_json
from agents.memory.diary import append_entry
from agents.core.heartbeat import log_event

router = APIRouter()


class GenerateIn(BaseModel):
    text: str
    intent: str | None = None
    priority: str | None = None
    depth: str | None = None  # "fast"|"core"|"deep"|"auto"


@router.post("/generate")
def generate(body: GenerateIn):
    # update conversation state for scheduler
    try:
        rt = runtime_dir()
        st = rt / "state"
        st.mkdir(parents=True, exist_ok=True)
        conv_path = st / "conversation_state.json"
        conv = read_json(conv_path, default={})
        conv["schema"] = "lira.conversation_state.v1"
        conv["last_user_ts"] = time.time()
        conv["last_intent"] = body.intent or "unknown"
        write_json(conv_path, conv)
    except Exception:
        pass

    # build context packet
    trace_id = None
    try:
        build_context_packet(text=body.text, intent=body.intent or "unknown", priority=body.priority or "normal")
        # read trace_id from runtime/state/context_packet.json
        ctx = read_json(runtime_dir() / "state" / "context_packet.json", default={})
        if isinstance(ctx, dict):
            trace_id = ctx.get("trace_id")
    except Exception:
        pass

    # runtime event trail for alignment/night cycle
    try:
        log_event("user_input", {"text": body.text})
    except Exception:
        pass

    phi = analyze_text(body.text)
    reply = route(body.text, depth=body.depth or "auto")

    try:
        log_event("assistant_output", {"text": reply})
    except Exception:
        pass

    # diary trail (append-only)
    try:
        append_entry({
            "kind": "heartbeat",
            "trace_id": trace_id,
            "intent": body.intent or "unknown",
            "priority": body.priority or "normal",
            "depth": body.depth or "auto",
            "user_text": body.text,
            "reply": (reply or "")[:5000],
            "phi": phi,
        })
    except Exception:
        pass
    return {"input": body.text, "phi": phi, "reply": reply}
