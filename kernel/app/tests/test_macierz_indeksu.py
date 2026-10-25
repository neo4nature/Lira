from __future__ import annotations
import json
import random
import pytest
from agents.memory import macierz_indeksu
from agents.memory import semantic_retrieval as retrieval
DIM = 16

def _rek(i, vec, kind='session', path=None, logical=None):
    src = {'kind': kind, 'offset': i}
    if path:
        src['path'] = path
    if logical:
        src['logical_source_id'] = logical
    return {'schema': 'lira.embedding_record.v1', 'id': f'{kind}:{i}', 'source': src, 'text': f'fragment {i}', 'model': 'mxbai', 'vector': vec}

def _wektory(n, seed):
    los = random.Random(seed)
    return [[los.uniform(-1, 1) for _ in range(DIM)] for _ in range(n)]

def _zapisz(plik, rekordy, tryb='w', koniec='\n'):
    with plik.open(tryb, encoding='utf-8') as f:
        for r in rekordy:
            f.write((r if isinstance(r, str) else json.dumps(r)) + '\n')
        if koniec != '\n':
            f.write(koniec)

@pytest.fixture
def pamiec(tmp_path, monkeypatch):
    (tmp_path / 'embeddings').mkdir()
    plik = tmp_path / 'embeddings' / 'embeddings_index.jsonl'
    stan = {'q': None, 'nieswieze': set(), 'logiczne': {}}
    monkeypatch.setattr(retrieval, 'memory_dir', lambda: tmp_path)
    monkeypatch.setattr(retrieval, '_ollama_embed', lambda model, text, **kw: stan['q'])
    monkeypatch.setattr(retrieval, '_base_search_by_vec', None)
    monkeypatch.setattr(retrieval, '_turmalin_iter_recent', None)
    monkeypatch.setattr(retrieval, '_przystawka_swiezosci', lambda: (stan['nieswieze'], stan['logiczne']))
    monkeypatch.setattr(macierz_indeksu, '_MACIERZ', macierz_indeksu.MacierzIndeksu())
    return (plik, stan, tmp_path)

def _obie(monkeypatch, stan, q, top_k):
    stan['q'] = q
    monkeypatch.setenv('LIRA_RECALL_MACIERZ', '0')
    wzor = retrieval.search('pytanie', top_k=top_k)
    monkeypatch.setenv('LIRA_RECALL_MACIERZ', '1')
    nowa = retrieval.search('pytanie', top_k=top_k)
    return (wzor, nowa)

def _indeks(tmp_path):
    w = _wektory(40, 1)
    rek = [_rek(i, w[i]) for i in range(40)]
    rek += [_rek(100 + i, w[i % 5]) for i in range(12)]
    rek.append(_rek(200, w[3], kind='file', path=str(tmp_path / 'shared_library' / 'import_staging' / 'raw' / 'x.txt')))
    rek.append(_rek(201, w[7], kind='file', path=str(tmp_path / 'diary' / 'd.md')))
    rek.append(_rek(202, [0.5] * (DIM + 1)))
    rek.append('{to nie jest json')
    rek.append({'schema': 'inny', 'id': 'x', 'vector': w[0]})
    return (w, rek)

@pytest.mark.parametrize('top_k', [1, 6, 20, 80])
def test_ten_sam_wynik_co_przeglad_pliku(pamiec, monkeypatch, top_k):
    plik, stan, tmp = pamiec
    w, rek = _indeks(tmp)
    _zapisz(plik, rek)
    stan['nieswieze'] = {'session:2', 'session:101'}
    stan['logiczne'] = {str(tmp / 'diary' / 'd.md'): 'plik:dziennik'}
    for q in (w[2], w[3], _wektory(1, 9)[0]):
        wzor, nowa = _obie(monkeypatch, stan, q, top_k)
        assert nowa == wzor
        ids = [r['id'] for r in nowa]
        assert 'session:2' not in ids and 'file:200' not in ids

def test_remisy_w_kolejnosci_pliku(pamiec, monkeypatch):
    plik, stan, tmp = pamiec
    w = _wektory(3, 2)
    _zapisz(plik, [_rek(i, w[i % 3]) for i in range(30)])
    wzor, nowa = _obie(monkeypatch, stan, w[1], 15)
    assert [r['id'] for r in nowa] == [r['id'] for r in wzor]
    assert [r['id'] for r in nowa][:10] == [f'session:{i}' for i in range(1, 30, 3)]

def test_dopisane_niedopisane_i_przepisane(pamiec, monkeypatch):
    plik, stan, tmp = pamiec
    w, rek = _indeks(tmp)
    _zapisz(plik, rek)
    _obie(monkeypatch, stan, w[0], 6)
    m = macierz_indeksu.macierz()
    przed = len(m.id_rek)
    nowy = _wektory(1, 5)[0]
    _zapisz(plik, [_rek(300, nowy)], tryb='a')
    pol = json.dumps(_rek(301, nowy))
    with plik.open('a', encoding='utf-8') as f:
        f.write(pol[:len(pol) // 2])
    wzor, nowa = _obie(monkeypatch, stan, nowy, 6)
    assert nowa == wzor and nowa[0]['id'] == 'session:300'
    assert len(m.id_rek) == przed + 1
    with plik.open('a', encoding='utf-8') as f:
        f.write(pol[len(pol) // 2:] + '\n')
    wzor, nowa = _obie(monkeypatch, stan, nowy, 6)
    assert nowa == wzor and {r['id'] for r in nowa[:2]} == {'session:300', 'session:301'}
    assert len(m.id_rek) == przed + 2
    plik.unlink()
    _zapisz(plik, [_rek(i, w[i]) for i in range(5)])
    wzor, nowa = _obie(monkeypatch, stan, w[4], 6)
    assert nowa == wzor and len(m.id_rek) == 5

def test_karty_zrodel_tez_identyczne(pamiec, monkeypatch):
    plik, stan, tmp = pamiec
    w, rek = _indeks(tmp)
    _zapisz(plik, rek)
    monkeypatch.setenv('LIRA_SOURCE_CARDS', '1')
    wzor, nowa = _obie(monkeypatch, stan, w[6], 5)
    assert nowa == wzor

def test_blad_macierzy_wraca_do_przegladu_pliku(pamiec, monkeypatch):
    plik, stan, tmp = pamiec
    w, rek = _indeks(tmp)
    _zapisz(plik, rek)

    def padnij(*a, **k):
        raise RuntimeError('zepsuta macierz')
    monkeypatch.setattr(macierz_indeksu.MacierzIndeksu, 'najlepsze', padnij)
    wzor, nowa = _obie(monkeypatch, stan, w[1], 6)
    assert nowa == wzor and nowa

def _rek_lira(i, v):
    return {'schema': 'lira.embedding_record.v1', 'id': str(i), 'vector': v, 'source': {'kind': 'session'}, 'text': str(i)}

def test_dlugi_remis_w_kolejnosci_pliku(tmp_path):
    p = tmp_path / 'index.jsonl'
    p.write_text(''.join((json.dumps(_rek_lira(i, [1.0, 0.0])) + '\n' for i in range(1000))))
    got = macierz_indeksu.MacierzIndeksu().najlepsze(p, [1.0, 0.0], 6, lambda r: True, set(), lambda r: r)
    assert [r['id'] for _, r in got] == [str(i) for i in range(6)]

def test_remis_float32_nie_chowa_prawdziwie_najlepszego(tmp_path):
    p = tmp_path / 'index.jsonl'
    rows = [_rek_lira(i, [1.0, 1.0 + 1e-05]) for i in range(1000)]
    rows[999] = _rek_lira(999, [1.0, 1.0])
    p.write_text(''.join((json.dumps(r) + '\n' for r in rows)))
    got = macierz_indeksu.MacierzIndeksu().najlepsze(p, [1.0, 1.0], 1, lambda r: True, set(), lambda r: r)
    assert got[0][1]['id'] == '999'

@pytest.mark.parametrize('top_k', [1, 6, 30])
def test_search_dlugi_remis_jak_stara_droga(pamiec, monkeypatch, top_k):
    plik, stan, tmp = pamiec
    v = [1.0] + [0.0] * (DIM - 1)
    _zapisz(plik, [_rek(i, v) for i in range(1000)] + [_rek(2000 + i, w) for i, w in enumerate(_wektory(50, 4))])
    stan['nieswieze'] = {'session:0', 'session:3'}
    wzor, nowa = _obie(monkeypatch, stan, v, top_k)
    assert nowa == wzor

def test_prawie_remisy_float32_jak_dokladny_ranking(tmp_path):
    np = pytest.importorskip('numpy')
    rng = np.random.default_rng(0)
    for proba in range(40):
        base = rng.normal(size=64)
        vecs = [base + rng.normal(size=64) * 10 ** rng.uniform(-9, -5) for _ in range(120)]
        q = base + rng.normal(size=64) * 1e-06
        p = tmp_path / f'i{proba}.jsonl'
        p.write_text(''.join((json.dumps(_rek_lira(i, v.tolist())) + '\n' for i, v in enumerate(vecs))))
        got = [r['id'] for _, r in macierz_indeksu.MacierzIndeksu().najlepsze(p, q.tolist(), 3, lambda r: True, set(), lambda r: r)]
        qn = np.linalg.norm(q) + 1e-12
        sc = sorted(((-float(np.dot(q, v) / (qn * (np.linalg.norm(v) + 1e-12))), i) for i, v in enumerate(vecs)))
        assert got == [str(i) for _, i in sc[:3]], proba

def test_rozgrzewka_w_tle_i_gotowosc(pamiec, monkeypatch):
    plik, stan, tmp = pamiec
    w, rek = _indeks(tmp)
    _zapisz(plik, rek)
    monkeypatch.setenv('LIRA_RECALL_MACIERZ', '1')
    m = macierz_indeksu.macierz()
    assert retrieval.macierz_gotowa(rozgrzewaj=False) is False and m._watek is None
    assert retrieval.macierz_gotowa() is False
    m._watek.join(10)
    assert retrieval.macierz_gotowa() is True and len(m.id_rek) > 0
    zbudowana = (m.inode, m.przeczytane)
    wzor, nowa = _obie(monkeypatch, stan, w[2], 6)
    assert nowa == wzor and (m.inode, m.przeczytane) == zbudowana

def test_gotowa_gdy_macierz_wylaczona(pamiec, monkeypatch):
    plik, stan, tmp = pamiec
    _zapisz(plik, _indeks(tmp)[1])
    monkeypatch.setenv('LIRA_RECALL_MACIERZ', '0')
    assert retrieval.macierz_gotowa() is True and macierz_indeksu.macierz()._watek is None

def _rozgrzana(pamiec, monkeypatch, rekordy):
    plik, stan, tmp = pamiec
    _zapisz(plik, rekordy)
    monkeypatch.setenv('LIRA_RECALL_MACIERZ', '1')
    m = macierz_indeksu.macierz()
    assert retrieval.macierz_gotowa() is False
    m._watek.join(10)
    assert retrieval.macierz_gotowa(rozgrzewaj=False) is True
    return (plik, stan, tmp, m)

def test_bramka_nie_czeka_na_lock_w_trakcie_budowy(pamiec, monkeypatch):
    import threading
    plik, stan, tmp = pamiec
    _zapisz(plik, [_rek(0, [1.0] + [0.0] * (DIM - 1))])
    monkeypatch.setenv('LIRA_RECALL_MACIERZ', '1')
    m = macierz_indeksu.macierz()
    wszedl, pusc = (threading.Event(), threading.Event())
    oryg = m._czytaj_od

    def wolno(*a):
        wszedl.set()
        assert pusc.wait(5)
        return oryg(*a)
    monkeypatch.setattr(m, '_czytaj_od', wolno)
    try:
        assert retrieval.macierz_gotowa() is False and wszedl.wait(5)
        for _ in range(100):
            assert retrieval.macierz_gotowa() is False
    finally:
        pusc.set()
        m._watek.join(10)
    assert retrieval.macierz_gotowa(rozgrzewaj=False) is True

@pytest.mark.parametrize('zmiana', ['podmieniony', 'skrocony'])
def test_podmieniony_albo_skrocony_indeks_nie_jest_gotowy(pamiec, monkeypatch, zmiana):
    plik, stan, tmp, m = _rozgrzana(pamiec, monkeypatch, _indeks(pamiec[2])[1])
    if zmiana == 'podmieniony':
        nowy = tmp / 'nowy.jsonl'
        _zapisz(nowy, [_rek(1, _wektory(1, 7)[0])])
        nowy.replace(plik)
    else:
        with plik.open('r+') as f:
            f.truncate(10)
    assert retrieval.macierz_gotowa(rozgrzewaj=False) is False
    assert retrieval.macierz_gotowa() is False
    m._watek.join(10)
    assert retrieval.macierz_gotowa(rozgrzewaj=False) is True
    wzor, nowa = _obie(monkeypatch, stan, _wektory(1, 7)[0], 6)
    assert nowa == wzor

def test_duza_zaleglosc_doczytywana_w_tle(pamiec, monkeypatch):
    plik, stan, tmp, m = _rozgrzana(pamiec, monkeypatch, _indeks(pamiec[2])[1])
    monkeypatch.setattr(macierz_indeksu, 'ZALEGLOSC_MAX', 100)
    przed = len(m.id_rek)
    _zapisz(plik, [_rek(500 + i, v) for i, v in enumerate(_wektory(5, 8))], tryb='a')
    assert retrieval.macierz_gotowa(rozgrzewaj=False) is False
    assert retrieval.macierz_gotowa() is False
    m._watek.join(10)
    assert retrieval.macierz_gotowa(rozgrzewaj=False) is True and len(m.id_rek) == przed + 5

@pytest.mark.parametrize('ktore', [1, 2])
def test_blad_odczytu_nie_zostawia_polowicznej_macierzy(pamiec, monkeypatch, ktore):
    plik, stan, tmp = pamiec
    w, rek = _indeks(tmp)
    _zapisz(plik, rek)
    monkeypatch.setenv('LIRA_RECALL_MACIERZ', '1')
    m = macierz_indeksu.macierz()
    if ktore == 2:
        retrieval.macierz_gotowa()
        m._watek.join(10)
        _zapisz(plik, [_rek(600 + i, v) for i, v in enumerate(_wektory(4, 9))], tryb='a')
    oryg, wywolania = (m._czytaj_od, [])

    def raz_pada(sciezka, od, przepusc):
        wywolania.append(od)
        if len(wywolania) == 1:
            oryg(sciezka, od, przepusc)
            raise OSError('chwilowy błąd odczytu')
        return oryg(sciezka, od, przepusc)
    monkeypatch.setattr(m, '_czytaj_od', raz_pada)
    monkeypatch.setattr(macierz_indeksu, 'ZALEGLOSC_MAX', 0)
    assert retrieval.macierz_gotowa() is False
    m._watek.join(10)
    assert not m.gotowa()
    assert retrieval.macierz_gotowa() is False
    m._watek.join(10)
    assert retrieval.macierz_gotowa(rozgrzewaj=False) is True
    swieza = macierz_indeksu.MacierzIndeksu()
    swieza.odswiez(plik, retrieval._poza_pracownia)
    assert m.id_rek == swieza.id_rek
    wzor, nowa = _obie(monkeypatch, stan, w[3], 20)
    assert nowa == wzor
