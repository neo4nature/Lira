# Lira (local)

Docelowa lokalizacja: `<LIRA_ROOT>`.

## 1) Instalacja (Ubuntu)

```bash
cd <LIRA_ROOT>
bash scripts/install_ubuntu.sh
```

## 2) Ustawienie ścieżek (.env)

```bash
cd <LIRA_ROOT>
bash scripts/update_env_paths.sh
```

## 3) Tekst (CLI)

```bash
cd <LIRA_ROOT>/app
source ../.venv/bin/activate
python3 run_text.py
```

## 4) Głos (STT+TTS)

Wymaga modelu Vosk w: `../models/vosk/pl`.

```bash
cd <LIRA_ROOT>/app
source ../.venv/bin/activate
python3 run_voice.py
```

## 5) Nocny cykl

```bash
cd <LIRA_ROOT>/app
bash run_night_cycle.sh
```

## 6) API (FastAPI)

```bash
cd <LIRA_ROOT>/app
source ../.venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8080
```

Endpointy:
- `GET /health`
- `POST /heartbeat/generate`
- `POST /speak/`
- `GET /memory/index`
- `POST /phi/analyze`
- `POST /phi/bridge`
