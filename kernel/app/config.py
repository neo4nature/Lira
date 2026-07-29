import os
from pathlib import Path


def _normalize_root(p: Path) -> Path:
    """Allow PROJECT_ROOT to be set to either kernel root or app dir."""
    p = p.resolve()
    if (p / "app").exists() and (p / "data").exists():
        return (p / "app").resolve()
    return p


def _load_dotenv_once() -> None:
    """Ładuj .env jeśli DATA_ROOT/LIRA_DATA_ROOT nie są ustawione w środowisku.

    Szuka .env w katalogu tego pliku (app/.env).
    Idempotentny — nie nadpisuje już ustawionych zmiennych.
    """
    if os.getenv("LIRA_DATA_ROOT") or os.getenv("DATA_ROOT"):
        return
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


_load_dotenv_once()


def env(name: str, default: str = "") -> str:
    """Read environment variable with support for LIRA_* aliases."""
    if not name.startswith("LIRA_"):
        v = os.getenv(f"LIRA_{name}")
        if v is not None and v != "":
            return v
    return os.getenv(name, default)


def project_root() -> Path:
    """Absolute path to code root (…/Lira/app)."""
    return _normalize_root(Path(env("PROJECT_ROOT", str(Path(__file__).resolve().parent))))


def data_root() -> Path:
    """Absolute path to data root (…/Lira/data)."""
    default = project_root().parent.parent / "data" if (project_root().name=="app" and (project_root().parent.parent/"data").exists()) else project_root().parent / "data"
    return Path(env("DATA_ROOT", str(default))).resolve()


def memory_dir() -> Path:
    return Path(env("MEMORY_DIR", str(data_root() / "memory"))).resolve()


def runtime_dir() -> Path:
    return Path(env("RUNTIME_DIR", str(data_root() / "runtime"))).resolve()


def shared_context_dir() -> Path:
    return Path(env("SHARED_CONTEXT_DIR", str(data_root() / "shared_context"))).resolve()

def logs_root() -> Path:
    """Absolute path to logs root."""
    default = project_root().parent / "logs"
    return Path(env("LOGS_ROOT", str(default))).resolve()

def archive_root() -> Path:
    """Absolute path to archives (cold storage, e.g., SD)."""
    default = project_root().parent / "archives"
    return Path(env("ARCHIVE_ROOT", str(default))).resolve()

def secure_root() -> Path:
    """Absolute path to encrypted emergency storage (dm-0)."""
    default = project_root().parent / "secure"
    return Path(env("SECURE_ROOT", str(default))).resolve()


def rebuild_dir() -> Path:
    return Path(env("REBUILD_DIR", str(runtime_dir() / "rebuild"))).resolve()


def queue_file() -> Path:
    return Path(env("QUEUE_FILE", str(runtime_dir() / "queue" / "queue_tasks.json"))).resolve()


def external_medium_ready(root: Path) -> tuple[bool, str]:
    """Czy ścieżka leży na naprawdę podpiętym nośniku zewnętrznym.

    Archiwa i zrzuty awaryjne siedzą poza dyskiem systemowym. Pytanie o
    `exists()` albo o prawo zapisu odpowiada na coś innego, niż nam chodzi:
    gdy karty nie ma, `mkdir(parents=True)` utworzy jej punkt montowania jako
    zwykły katalog na dysku systemowym. Kopia będzie udawała kopię, dane trafią
    na pełny dysk, a nośnik już się w tym miejscu nie zamontuje. Dlatego pytamy
    o granicę systemu plików. Punkt montowania może leżeć wyżej niż sam cel
    (cel bywa zwykłym katalogiem na podpiętym dysku), więc szukamy go w górę.
    """
    probe = Path(root).resolve()
    while not probe.exists():
        if probe.parent == probe:
            return False, "brak katalogu głównego"
        probe = probe.parent

    mount = probe
    while not os.path.ismount(mount) and mount.parent != mount:
        mount = mount.parent
    # ten sam nośnik co katalog główny = zwykły katalog na dysku systemowym,
    # choćby nazywał się jak karta; łapie też bind mount udający nośnik
    if mount.stat().st_dev == Path("/").stat().st_dev:
        return False, f"nośnik niezamontowany (najbliższy punkt montowania {mount} to dysk systemowy)"
    if not os.access(probe, os.W_OK):
        return False, f"nośnik zamontowany, ale bez prawa zapisu ({probe})"
    return True, ""


def ensure_dirs():
    guarded = {archive_root(), secure_root()}
    for p in [
        memory_dir(),
        memory_dir() / "root",
        memory_dir() / "diary",
        memory_dir() / "lira",
        memory_dir() / "mika",
        memory_dir() / "natalia",
        memory_dir() / "images",
        memory_dir() / "embeddings",
        runtime_dir(),
        runtime_dir() / "queue",
        runtime_dir() / "state",
        runtime_dir() / "logs",
        logs_root(),
        archive_root(),
        secure_root(),
        runtime_dir() / "cache",
        runtime_dir() / "backups",
        shared_context_dir(),
        shared_context_dir() / "events",
        shared_context_dir() / "state",
        rebuild_dir(),
    ]:
        # Nośników zewnętrznych nie tworzymy na ślepo: gdy karty nie ma,
        # mkdir zrobiłby jej punkt montowania na dysku systemowym.
        if p in guarded and not external_medium_ready(p)[0]:
            continue
        try:
            p.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            pass


def paths() -> dict:
    md = memory_dir()
    rt = runtime_dir()
    sc = shared_context_dir()
    rb = rebuild_dir()
    return {
        "PROJECT_ROOT": project_root(),
        "DATA_ROOT": data_root(),
        "MEMORY_DIR": md,
        "RUNTIME_DIR": rt,
        "STATE_DIR": rt / "state",
        "SHARED_CONTEXT_DIR": sc,
        "REBUILD_DIR": rb,
        "QUEUE_FILE": queue_file(),
        "MEMORY_INDEX": md / "root" / "memory_index.json",
        "EMBEDDINGS_DIR": md / "embeddings",
        "EMBEDDINGS_INDEX": md / "embeddings" / "embeddings_index.jsonl",
        # Base (stable) embedding index (SQLite)
        "BASE_INDEX_DB": md / "root" / "base_index.sqlite3",
        # Turmalin (temporary) embedding cache (JSONL)
        "TURMALIN_CACHE": md / "embeddings" / "turmalin_cache.jsonl",
        "TURMALIN_STATE": rt / "state" / "turmalin_state.json",
        # Memory state written by NightCycle/TurmalinLoop (read by DecisionLoop)
        "MEMORY_STATE": rt / "state" / "memory_state.json",
        "MEMORY_MODE": rt / "state" / "memory_mode.json",
        "EVENTS_DIR": sc / "events",
        "RUNTIME_STREAM": (sc / "events" / "runtime.jsonl"),
        "BASE_INDEX": md / "root" / "base_index.json",
        "WATCHDOG_REPORT": rt / "state" / "watchdog_report.json",
        "COLLECTOR_STATE": rt / "state" / "collector_state.json",
        "CORE_MEMORY_REBUILD": rb / "core_memory_rebuild.json",
        "CORE_MEMORY_REBUILD_MD": rb / "core_memory_rebuild.md",
        "REFLECTIVE_LOG_NIGHT": rb / "reflective_log_night.json",
        "REFLECTIVE_JOURNAL": rb / "reflective_journal_log.json",
        "SNAPSHOTS_DIR": md / "snapshots",
        "SNAPSHOTS_MD_DIR": md / "snapshots_md",
        "DIARY_DIR": md / "diary",
    }
