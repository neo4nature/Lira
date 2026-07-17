import sys, tempfile, os
from pathlib import Path

APP = Path("${LIRA_ROOT:-/opt/lira}/kernel/app")
sys.path.insert(0, str(APP))

import governance.capsule_bus as bus
import governance.gg_dispatcher as gg

tmp = Path(tempfile.mkdtemp()) / "test_bus.db"
bus.init_db(tmp)
gg.DB_PATH = tmp  # guardy w gg czytają DB_PATH z własnego modułu

results = []

def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)

# 1. content_hash deterministyczny i czuły na treść
a = {"payload_json": {"title": "T1", "summary": "S1", "text": ""}}
b = {"payload_json": '{"title": "T1", "summary": "S1", "text": ""}'}
c = {"payload_json": {"title": "T2", "summary": "S1", "text": ""}}
check("hash: dict==json-string", gg._content_hash(a) == gg._content_hash(b))
check("hash: rozna tresc rozny hash", gg._content_hash(a) != gg._content_hash(c))

# 2. signature_repeats — 3 identyczne kapsuly system->Lira
ids = []
for i in range(3):
    cid = bus.send_capsule(
        type="fact", weight="commentary", layer="shared", owner="shared",
        sender="system", recipient="Lira",
        payload={"title": "Powtorka", "summary": "ta sama tresc"},
        project="ecosystem_monitor", db_path=tmp)
    ids.append(cid)

def fetch(cid):
    with bus.get_conn(tmp) as conn:
        return dict(conn.execute("SELECT * FROM capsules WHERE id=?", (cid,)).fetchone())

third = fetch(ids[2])
check("signature: trzecia kopia widzi 2 powtorki", gg._signature_repeats(third) == 2)
first_unique = bus.send_capsule(
    type="fact", weight="commentary", layer="shared", owner="shared",
    sender="system", recipient="Lira",
    payload={"title": "Unikalna", "summary": "inna tresc"},
    project="ecosystem_monitor", db_path=tmp)
check("signature: unikalna widzi 0", gg._signature_repeats(fetch(first_unique)) == 0)

# 3. pair guard — 4 kapsuly agentow w watku bez Neo
tid = "watek-pary"
for i, snd in enumerate(["Lira", "Soryel", "Lira", "Soryel"]):
    bus.send_capsule(
        type="interpretation", weight="commentary", layer="working", owner=snd,
        sender=snd, recipient="shared", payload={"title": f"w{i}", "summary": "x"},
        thread_id=tid, project="ecosystem_monitor", db_path=tmp)
check("pair: 4 agentow bez Neo => 4", gg._agent_msgs_since_neo(tid) == 4)

# glos Neo zeruje licznik
bus.send_capsule(
    type="fact", weight="commentary", layer="shared", owner="shared",
    sender="Neo", recipient="shared", payload={"title": "glos", "summary": "neo mowi"},
    thread_id=tid, project="ecosystem_monitor", db_path=tmp)
check("pair: po glosie Neo => 0", gg._agent_msgs_since_neo(tid) == 0)

# 4. mark_answered z details
resp = bus.send_capsule(
    type="decision", weight="commentary", layer="shared", owner="shared",
    sender="Soryel", recipient="shared", payload={"title": "d"}, db_path=tmp)
bus.mark_answered(ids[0], "dispatcher:Soryel", resp, details={"bridge_target": "noop"}, db_path=tmp)
row = fetch(ids[0])
check("mark_answered+details: status answered", row["status"] == "answered")
check("mark_answered+details: response id", row["response_capsule_id"] == resp)

# 5. pusty thread_id => 0 (brak guarda)
check("pair: thread_id None => 0", gg._agent_msgs_since_neo(None) == 0)

fails = [n for n, ok in results if not ok]
print("\n%d/%d PASS" % (len(results) - len(fails), len(results)))
sys.exit(1 if fails else 0)
