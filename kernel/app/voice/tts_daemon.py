#!/usr/bin/env python3
"""TTS daemon — czyta ostatnie odpowiedzi asystenta, ale mówi tylko treść ludzką.

Wersja v2:
- mocniejsze czyszczenie metadanych kapsuł i nagłówków technicznych
- heurystyczne wymuszenie polskiego głosu dla polskich treści
- logowanie: raw -> clean -> voice, żeby łatwo sprawdzić, co naprawdę idzie do TTS
- zachowuje domyślne głosy z .env, nie podmienia ich samowolnie
"""
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.tts import detect_lang
from voice.ssml_emotion import wrap_ssml

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
VOICE_PL = os.getenv("SORYEL_TTS_VOICE_PL", "pl-PL-ZofiaNeural")
VOICE_EN = os.getenv("SORYEL_TTS_VOICE_EN", "en-GB-SoniaNeural")
BASE_PITCH = os.getenv("SORYEL_TTS_PITCH", "+5Hz")
BASE_RATE = os.getenv("SORYEL_TTS_RATE", "-5%")
POLL_INTERVAL = float(os.getenv("TTS_DAEMON_POLL", "1.2"))
MAX_CHARS = int(os.getenv("SORYEL_SPEAK_MAX", "0"))  # 0 = bez limitu, chunked synthesis
CHUNK_SIZE = int(os.getenv("SORYEL_SPEAK_CHUNK", "2400"))  # max znaków na chunk (~3 zdania)
CHUNK_SIZE_FIRST = int(os.getenv("SORYEL_SPEAK_CHUNK_FIRST", "400"))  # pierwszy chunk krótki — szybki start
MIN_CHARS = int(os.getenv("SORYEL_SPEAK_MIN", "15"))
SETTLE_SECONDS = float(os.getenv("TTS_DAEMON_SETTLE", "3.5"))
DEBUG = os.getenv("TTS_DAEMON_DEBUG", "1") == "1"
TTS_SINK = os.getenv("TTS_SINK", "")

MODEL_LINE_RE = re.compile(
    r"""^
    \s*(?:
        claude(?:[ -].*)?|gpt(?:[ -].*)?|o\d(?:[ -].*)?|gemini(?:[ -].*)?|llama(?:[ -].*)?|
        qwen(?:[ -].*)?|phi\d*(?:[ -].*)?|sonnet(?:[ -].*)?|opus(?:[ -].*)?|haiku(?:[ -].*)?
    )\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

POLISH_HINTS = {
    "że", "się", "nie", "jest", "czy", "ty", "ci", "to", "tak", "już", "teraz",
    "kochanie", "widzę", "dziękuję", "proszę", "który", "która", "które", "żeby",
    "odpowiedź", "pytanie", "warstwa", "właściciel", "projekt", "treść", "opis"
}

KV_META_PREFIXES = (
    "model", "version", "engine", "voice", "typ", "type", "warstwa", "layer", "waga",
    "weight", "owner", "właściciel", "projekt", "project", "route", "route target",
    "confidence", "policy", "phase", "faza", "alignment", "energia", "energy", "mode",
    "status", "recipient", "sender", "title", "tytuł", "open question", "otwarte pytanie"
)


def find_latest_session() -> str | None:
    files = glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True)
    return max(files, key=os.path.getmtime) if files else None


def _strip_code_and_markup(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`\n]+`", " ", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_\n]+)_{1,3}", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b\w{2,8}://\S+", " ", text)
    text = re.sub(r"/[a-zA-Z0-9_./-]{5,}", " ", text)
    return text


def _is_meta_line(s: str) -> bool:
    s = s.strip()
    if not s:
        return True
    low = s.lower()
    if low.startswith("[kapsuła") or low.startswith("[capsule"):
        return True
    if MODEL_LINE_RE.match(s):
        return True
    if "|" in s and sum(1 for p in s.split("|") if p.strip()) >= 2 and len(s) < 180:
        return True
    if any(low.startswith(prefix + ":") or low.startswith(prefix + "=") for prefix in KV_META_PREFIXES):
        return True
    if re.match(r"^\s*[A-Z_]{3,}[=:].*$", s):
        return True
    if re.match(r"^\s*(?:uuid|id|pid|json|yaml|toml|env|ssml)\b.*$", s, re.IGNORECASE):
        return True
    if re.match(r"^\s*[\[{\"]", s) and len(s) < 120:
        return True
    return False


def clean(text: str) -> str:
    text = _strip_code_and_markup(text)
    kept = []
    for raw in text.splitlines():
        s = raw.strip()
        if _is_meta_line(s):
            continue
        s = re.sub(r"^\s*[-*+]\s+", "", s)
        s = re.sub(r"^\s*\d+[\.)]\s+", "", s)
        s = re.sub(r"^\s*(?:tytuł|title|opis|description|treść|content|otwarte pytanie|open question)\s*:\s*", "", s, flags=re.I)
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            continue
        kept.append(s)

    # odetnij techniczne linie z początku aż do pierwszej normalnej wypowiedzi
    while kept and (_is_meta_line(kept[0]) or len(re.findall(r"[A-Za-zÀ-ÿĄąĆćĘęŁłŃńÓóŚśŹźŻż]", kept[0])) < 6):
        kept.pop(0)

    text = "\n".join(kept).strip()
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)

    # usuń z początku pierwsze zdanie jeśli nadal wygląda meta-technicznie
    sentences = re.split(r"(?<=[.!?])\s+", text)
    while sentences:
        first = sentences[0].strip()
        low = first.lower()
        if _is_meta_line(first) or any(k in low for k in ["model", "version", "voice", "route target", "confidence"]):
            sentences.pop(0)
        else:
            break
    return " ".join(s.strip() for s in sentences if s.strip()).strip()


def _extract_text_from_content(content) -> str:
    if isinstance(content, list):
        blocks = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                txt = str(block.get("text", "")).strip()
                if txt:
                    blocks.append(txt)
        return "\n".join(blocks).strip()
    return str(content).strip()


def get_last_assistant_entry(session_path: str) -> tuple[str, str] | None:
    last_uuid = None
    last_text = None
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                msg = entry.get("message", {})
                if msg.get("role") != "assistant":
                    continue
                text = _extract_text_from_content(msg.get("content", ""))
                if text:
                    last_uuid = entry.get("uuid", "")
                    last_text = text
    except Exception:
        pass
    return (last_uuid, last_text) if last_uuid and last_text else None


def split_into_chunks(text: str, chunk_size: int = 2400, first_chunk_size: int = 0) -> list[str]:
    """Dzieli tekst na kawałki po granicach zdań. Pierwszy chunk może być krótszy (szybki start TTS)."""
    if chunk_size <= 0 or len(text) <= (first_chunk_size or chunk_size):
        return [text] if text else []
    chunks = []
    remaining = text
    is_first = True
    while remaining:
        cs = first_chunk_size if (is_first and first_chunk_size > 0) else chunk_size
        is_first = False
        if len(remaining) <= cs:
            chunks.append(remaining)
            break
        cut = max(
            remaining.rfind(".", 0, cs),
            remaining.rfind("!", 0, cs),
            remaining.rfind("?", 0, cs),
        )
        if cut < cs // 3:
            cut = remaining.rfind(" ", 0, cs)
        if cut < cs // 3:
            cut = cs
        chunks.append(remaining[: cut + 1].strip())
        remaining = remaining[cut + 1:].strip()
    return [c for c in chunks if c]


def detect_lang_safe(text: str) -> str:
    low = text.lower()
    diacritics = sum(ch in "ąćęłńóśźż" for ch in low)
    words = re.findall(r"[a-zA-ZÀ-ÿĄąĆćĘęŁłŃńÓóŚśŹźŻż]+", low)
    polish_hits = sum(1 for w in words if w in POLISH_HINTS)
    if diacritics >= 1 or polish_hits >= 2:
        return "pl"
    try:
        lang = detect_lang(text)
    except Exception:
        lang = "pl"
    # jeśli detektor zwrócił en, ale tekst i tak wygląda po polsku, zostań przy pl
    if lang != "pl":
        if diacritics >= 1 or polish_hits >= 1 or re.search(r"\b(?:nie|jest|czy|się|że|już|teraz|proszę|dziękuję)\b", low):
            return "pl"
    return lang


def main() -> None:
    print("[TTS daemon] Start — nasłuchuję nowych odpowiedzi Claude Code...", flush=True)
    print(f"[TTS daemon] Głos PL: {VOICE_PL} | Głos EN: {VOICE_EN} | poll: {POLL_INTERVAL}s", flush=True)

    last_spoken_uuid = None
    current_session = None

    while True:
        try:
            session = find_latest_session()
            if not session:
                time.sleep(POLL_INTERVAL)
                continue

            if session != current_session:
                current_session = session
                entry = get_last_assistant_entry(session)
                if entry:
                    last_spoken_uuid = entry[0]
                print(f"[TTS daemon] Sesja: {os.path.basename(session)}", flush=True)

            entry = get_last_assistant_entry(session)
            if not entry:
                time.sleep(POLL_INTERVAL)
                continue

            uuid, raw_text = entry
            if uuid == last_spoken_uuid:
                time.sleep(POLL_INTERVAL)
                continue

            time.sleep(SETTLE_SECONDS)
            entry2 = get_last_assistant_entry(session)
            if entry2 and entry2[0] != uuid:
                uuid, raw_text = entry2
            if uuid == last_spoken_uuid:
                time.sleep(POLL_INTERVAL)
                continue

            cleaned = clean(raw_text)
            if MAX_CHARS > 0 and len(cleaned) > MAX_CHARS:
                cleaned = cleaned[:MAX_CHARS]
            if not cleaned or len(cleaned) < MIN_CHARS:
                last_spoken_uuid = uuid
                time.sleep(POLL_INTERVAL)
                continue

            lang = detect_lang_safe(cleaned)
            voice = VOICE_PL if lang == "pl" else VOICE_EN
            from voice.ssml_emotion import detect_mood, _MOODS
            mood = detect_mood(cleaned)
            prosody = _MOODS.get(mood, _MOODS["neutral"])

            def _add_hz(a: str, b: str) -> str:
                try:
                    va = float(a.replace("Hz", "").replace("+", ""))
                    vb = float(b.replace("Hz", "").replace("+", ""))
                    total = va + vb
                    return f"{'+' if total >= 0 else ''}{total:.0f}Hz"
                except Exception:
                    return a

            def _add_pct(a: str, b: str) -> str:
                try:
                    va = float(a.replace("%", "").replace("+", ""))
                    vb = float(b.replace("%", "").replace("+", ""))
                    total = va + vb
                    return f"{'+' if total >= 0 else ''}{total:.0f}%"
                except Exception:
                    return a

            tts_pitch = _add_hz(BASE_PITCH, prosody.pitch)
            tts_rate  = _add_pct(BASE_RATE, prosody.rate)

            if DEBUG:
                print(f"[TTS daemon] lang={lang} voice={voice} mood={mood} pitch={tts_pitch} rate={tts_rate}", flush=True)
                print(f"[TTS daemon] RAW:   {repr(raw_text[:180])}", flush=True)
                print(f"[TTS daemon] CLEAN: {repr(cleaned[:180])}", flush=True)

            last_spoken_uuid = uuid

            try:
                import asyncio
                import subprocess
                import tempfile
                import edge_tts as _edge

                chunks = split_into_chunks(cleaned, CHUNK_SIZE, first_chunk_size=CHUNK_SIZE_FIRST)
                if DEBUG:
                    print(f"[TTS daemon] Chunks: {len(chunks)} (łącznie {len(cleaned)} znaków)", flush=True)

                async def _synth_chunk(txt: str, mp3: str) -> None:
                    comm = _edge.Communicate(txt, voice, rate=tts_rate, pitch=tts_pitch)
                    with open(mp3, "wb") as out:
                        async for ch in comm.stream():
                            if ch["type"] == "audio":
                                out.write(ch["data"])

                for i, chunk_text in enumerate(chunks):
                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                        mp3 = f.name
                    try:
                        asyncio.run(_synth_chunk(chunk_text, mp3))
                        if os.path.getsize(mp3) > 100:
                            wav = mp3.replace(".mp3", ".wav")
                            subprocess.run(
                                ["ffmpeg", "-y", "-i", mp3, "-ar", "22050", "-ac", "1", "-f", "wav", wav],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                            )
                            if TTS_SINK:
                                subprocess.run(["paplay", f"--device={TTS_SINK}", wav], check=False)
                            else:
                                subprocess.run(["aplay", "-q", wav], check=False)
                            try:
                                os.unlink(wav)
                            except Exception:
                                pass
                    finally:
                        try:
                            os.unlink(mp3)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[TTS daemon] TTS błąd: {e}", flush=True)

        except KeyboardInterrupt:
            print("\n[TTS daemon] Stop.")
            break
        except Exception as e:
            print(f"[TTS daemon] Błąd pętli: {e}", flush=True)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
