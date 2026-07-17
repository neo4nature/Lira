"""Test akceptacyjny szyny (ustalenie z Lirą 2026-06-12):
wymuszony timeout -> retry_pending -> wraca -> bez duplikatu; + stale lease recovery; + dead letter.
Caly flow przez prawdziwy xian_inbox_daemon.process_inbox na tymczasowej bazie.
"""
import sys, tempfile, functools
from pathlib import Path

APP = Path("${LIRA_ROOT:-/opt/lira}/kernel/app")
sys.path.insert(0, str(APP))

import governance.capsule_bus as bus
import governance.xian_inbox_daemon as xd

tmp = Path(tempfile.mkdtemp()) / "acc_bus.db"
bus.init_db(tmp)

# przekieruj caly dostep daemona na tymczasowa baze
bus.DB_PATH = tmp  # lokalne importy w _recent_chat_context/_round_peers_context
for fn in ("init_db", "read_capsules", "send_capsule", "update_status",
           "begin_processing", "mark_answered", "mark_retry", "recover_stale", "lease_heartbeat"):
    setattr(xd, fn, functools.partial(getattr(bus, fn), db_path=tmp))

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)

def fetch(cid):
    with bus.get_conn(tmp) as conn:
        return dict(conn.execute("SELECT * FROM capsules WHERE id=?", (cid,)).fetchone())

def xian_replies():
    with bus.get_conn(tmp) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM capsules WHERE sender='Xian' ORDER BY created_at").fetchall()]

def neo_msg(text, thread="moltbook-main"):
    return bus.send_capsule(
        type="chat_message", weight="commentary", layer="working", owner="shared",
        sender="Neo", recipient="Xian",
        payload={"kind": "chat_message", "text": text, "room": "moltbook-main"},
        thread_id=thread, project="moltbook", db_path=tmp)

# ===== TEST A: timeout -> retry_pending -> wraca -> bez duplikatu =====
cid = neo_msg("Xian, jak sie czujesz?")

xd._ask_xian = lambda prompt: ""          # wymuszony timeout (Ollama brak odpowiedzi)
n = xd.process_inbox()
row = fetch(cid)
check("A1: po timeoucie status=retry_pending", row["status"] == "retry_pending")
check("A2: attempt_count=1", row["attempt_count"] == 1)
check("A3: zero odpowiedzi Xiana po timeoucie", len(xian_replies()) == 0)
check("A4: claim zwolniony", row["claimed_by"] is None and row["lease_expires_at"] is None)

xd._ask_xian = lambda prompt: "Czuje sie dobrze, dziekuje Neo."   # Ollama wstala
n = xd.process_inbox()
row = fetch(cid)
replies = xian_replies()
check("A5: kapsula wrocila i jest answered", row["status"] == "answered")
check("A6: attempt_count=2 (retry oddzielny od tury)", row["attempt_count"] == 2)
check("A7: dokladnie JEDNA odpowiedz (bez duplikatu)", len(replies) == 1)
check("A8: response_capsule_id = id odpowiedzi", row["response_capsule_id"] == replies[0]["id"])
check("A9: odpowiedz spieta parent_id", replies[0]["parent_id"] == cid)

n = xd.process_inbox()   # trzeci cykl — kolejka pusta
check("A10: trzeci cykl nic nie robi, wciaz 1 odpowiedz", n == 0 and len(xian_replies()) == 1)

# ===== TEST B: stale lease recovery (padly daemon) =====
cid_b = neo_msg("Druga wiadomosc — daemon padnie w trakcie")
ok = bus.begin_processing(cid_b, "Xian", db_path=tmp)
check("B1: begin_processing przejal", ok and fetch(cid_b)["status"] == "in_progress")
# symulacja: lease wygasl (daemon umarl bez heartbeatu)
with bus.get_conn(tmp) as conn:
    conn.execute("UPDATE capsules SET lease_expires_at='2020-01-01T00:00:00+00:00' WHERE id=?", (cid_b,))
rec = bus.recover_stale("Xian", db_path=tmp)
check("B2: recover_stale odzyskal 1", rec == 1 and fetch(cid_b)["status"] == "retry_pending")
xd._ask_xian = lambda prompt: "Wrocilam po awarii."
xd.process_inbox()
row_b = fetch(cid_b)
check("B3: po recovery answered z dowodem", row_b["status"] == "answered" and row_b["response_capsule_id"])

# ===== TEST C: dead letter po max prob =====
cid_c = neo_msg("Trzecia — Ollama lezy na amen")
xd._ask_xian = lambda prompt: ""
for i in range(3):
    xd.process_inbox()
row_c = fetch(cid_c)
check("C1: po 3 probach blocked (dead letter)", row_c["status"] == "blocked")
check("C2: attempt_count=3", row_c["attempt_count"] == 3)
check("C3: blocked NIE wraca do kolejki", xd.process_inbox() == 0)

# ===== TEST D: druga tura rozmowy przechodzi (hop guard 5) =====
import governance.gg_dispatcher as gg
gg.DB_PATH = tmp
cid_d2 = bus.send_capsule(
    type="fact", weight="commentary", layer="shared", owner="shared",
    sender="Neo", recipient="Xian", payload={"kind": "chat_message", "text": "druga tura"},
    thread_id="moltbook-main", project="moltbook", iteration_index=2, db_path=tmp)
xd._ask_xian = lambda prompt: "Odpowiadam na druga ture."
xd.process_inbox()
row_d = fetch(cid_d2)
check("D1: kapsula z iteration_index=2 przetworzona (answered)", row_d["status"] == "answered")
hop_ok = int(row_d.get("iteration_index") or 0) <= gg.MAX_HOPS
check("D2: iteration 2 miesci sie w hop guard (<=5)", hop_ok)

fails = [name for name, ok in results if not ok]
print("\n%d/%d PASS" % (len(results) - len(fails), len(results)))
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
