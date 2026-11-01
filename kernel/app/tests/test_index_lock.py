"""PA-080: wspólna blokada pisarzy indeksu — test wieloprocesowy z wymuszonym przeplotem.

Warunek Liry (15.09): przeplot wymuszony barierą i porcjowaniem zapisu, nie `sleep`
ani szczęściem. Zdarzenia między procesami ustalają kolejność; limity czasu w `wait`
są tylko bezpiecznikiem przed zawieszeniem testu, nie mechanizmem.
"""
import fcntl
import json
import multiprocessing as mp
import os

import pytest

from agents.memory import index_lock as il

CZEKAJ = 10  # bezpiecznik, nie mechanizm


def _rekord(kto: str) -> str:
    linia = json.dumps({"id": kto, "text": kto * 16000}) + "\n"  # ~16 KB, jak prawdziwy rekord
    assert len(linia) > 3 * 5000  # kawałki 5000 muszą wypaść W ŚRODKU rekordu
    return linia


def _a_bez_ochrony(sciezka, pol, b_gotowe):
    linia = _rekord("A")
    with open(sciezka, "a", encoding="utf-8") as f:
        f.write(linia[:5000]); f.flush()
        pol.set()
        assert b_gotowe.wait(CZEKAJ)
        f.write(linia[5000:]); f.flush()


def _b_bez_ochrony(sciezka, pol, b_gotowe):
    assert pol.wait(CZEKAJ)
    with open(sciezka, "a", encoding="utf-8") as f:
        f.write(_rekord("B")); f.flush()
    b_gotowe.set()


def _a_z_ochrona(sciezka, pol, b_startuje, wynik):
    def w_srodku(nr):
        if nr != 0:
            return
        pol.set()
        # Twardy dowód wykluczenia: drugi deskryptor nie dostaje blokady w środku rekordu.
        fd = os.open(il.plik_blokady(sciezka), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            wynik.put("BLOKADA_WOLNA_W_SRODKU_REKORDU")
        except BlockingIOError:
            wynik.put("zajeta")
        finally:
            os.close(fd)
        assert b_startuje.wait(CZEKAJ)  # B już próbuje pisać, zanim A skończy
    with open(sciezka, "a", encoding="utf-8") as f:
        il.dopisz_linie(f, _rekord("A"), sciezka, kawalek=5000, po_kawalku=w_srodku)


def _b_z_ochrona(sciezka, pol, b_startuje):
    assert pol.wait(CZEKAJ)
    with open(sciezka, "a", encoding="utf-8") as f:
        b_startuje.set()
        il.dopisz_linie(f, _rekord("B"), sciezka)


def _uruchom(*cele):
    ctx = mp.get_context("fork")
    procesy = [ctx.Process(target=c, args=a) for c, a in cele]
    for p in procesy:
        p.start()
    for p in procesy:
        p.join(CZEKAJ * 2)
    assert all(p.exitcode == 0 for p in procesy), [p.exitcode for p in procesy]


def _poprawne(sciezka):
    linie = open(sciezka, encoding="utf-8").read().splitlines()
    out = []
    for l in linie:
        try:
            out.append(json.loads(l)["id"])
        except ValueError:
            out.append(None)
    return out


def test_bez_ochrony_przeplot_niszczy_rekord(tmp_path):
    """Punkt odniesienia: bez wspólnej blokady przeplot rozrywa rekord A (wymuszone, nie losowe)."""
    s = tmp_path / "embeddings_index.jsonl"
    ctx = mp.get_context("fork")
    pol, b_gotowe = ctx.Event(), ctx.Event()
    _uruchom((_a_bez_ochrony, (s, pol, b_gotowe)), (_b_bez_ochrony, (s, pol, b_gotowe)))
    assert None in _poprawne(s)


def test_z_ochrona_oba_rekordy_cale_i_w_kolejnosci(tmp_path):
    s = tmp_path / "embeddings_index.jsonl"
    ctx = mp.get_context("fork")
    pol, b_startuje, wynik = ctx.Event(), ctx.Event(), ctx.Queue()
    _uruchom((_a_z_ochrona, (s, pol, b_startuje, wynik)), (_b_z_ochrona, (s, pol, b_startuje)))
    assert wynik.get(timeout=CZEKAJ) == "zajeta"
    assert _poprawne(s) == ["A", "B"]


def test_rekord_bez_konca_linii_odrzucony(tmp_path):
    s = tmp_path / "embeddings_index.jsonl"
    with open(s, "a", encoding="utf-8") as f, pytest.raises(ValueError):
        il.dopisz_linie(f, '{"id": "x"}', s)
    assert s.read_text() == ""


def test_blokada_zwolniona_po_smierci_pisarza(tmp_path):
    """flock znika z procesem — martwy pisarz nie blokuje pozostałych."""
    s = tmp_path / "embeddings_index.jsonl"
    ctx = mp.get_context("fork")

    def umiera_w_srodku():
        with il.wylacznie(s):
            os._exit(1)

    p = ctx.Process(target=umiera_w_srodku)
    p.start(); p.join(CZEKAJ)
    with open(s, "a", encoding="utf-8") as f:
        il.dopisz_linie(f, _rekord("C"), s)
    assert _poprawne(s) == ["C"]
