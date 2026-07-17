from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from cognition.phi.phi_hook import analyze_text, analyze_pair, analyze_text_v2, analyze_pair_v2

router = APIRouter()


class AnalyzeIn(BaseModel):
    text: str


class BridgeIn(BaseModel):
    prev_text: str
    new_text: str


@router.post("/analyze")
def analyze(body: AnalyzeIn):
    return analyze_text(body.text)


@router.post("/bridge")
def bridge(body: BridgeIn):
    a = analyze_pair(body.prev_text, body.new_text)
    return {"paradigm": a.paradigm, "tension": a.tension, "bridge": a.bridge}


@router.post("/analyze_v2")
def analyze_v2(body: AnalyzeIn):
    return analyze_text_v2(body.text)


@router.post("/bridge_v2")
def bridge_v2(body: BridgeIn):
    return analyze_pair_v2(body.prev_text, body.new_text)
