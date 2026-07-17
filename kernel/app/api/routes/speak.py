from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from voice.tts import speak

router = APIRouter()


class SpeakIn(BaseModel):
    text: str


@router.post("/")
def speak_text(body: SpeakIn):
    # Fire and forget; returns immediately.
    speak(body.text)
    return {"spoken": True}
