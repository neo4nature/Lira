from __future__ import annotations
import argparse
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo
from agents.memory import retrieval_usage as usage
APP_ROOT = Path(__file__).resolve().parents[1]
WATEK = os.getenv('LIRA_MEMORY_THREAD', 'memory-main')
LONDYN = ZoneInfo('UTC')
KANALY = ('shared', 'duet')
MAX_ZAPYTANIE = 600
MAX_TOP = 20
MAX_KONTEKST = 5
MAX_TEKST = 600
SASIEDZTWO = timedelta(minutes=30)
KOPIA_W_CIAGU = timedelta(seconds=5)
RDZEN = 5
BM25_K1, BM25_B = (1.2, 0.75)
MIN_WSPOLNYCH = 2
KANAL_SLADU = 'stol_slowa'
BEZ_IMIENIA = 'stol_szukaj'
LUDZIE = tuple(v.strip() for v in os.getenv('LIRA_MEMORY_HUMANS', 'Operator').split(',') if v.strip())
MIN_POCZATEK_RUNDY = 6
PRZERWA_ROZMOWY = timedelta(minutes=60)
MAX_TEKST_WATKU = 320
ZYWE_OGNIWA = ('trafne', 'sporne', 'poprawione')

class BrakSzyny(RuntimeError):
    pass

def _domyslna_baza() -> Path:
    from governance.capsule_bus import DB_PATH
    return Path(DB_PATH)

def _polacz(db: Path) -> sqlite3.Connection:
    db = Path(db)
    if not db.is_file():
        raise BrakSzyny(f'brak pliku szyny: {db}')
    try:
        con = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise BrakSzyny(f'szyna niedostępna: {exc}') from exc
    con.row_factory = sqlite3.Row
    return con

def _zloz(text: str) -> str:
    text = unicodedata.normalize('NFKD', text.casefold().replace('ł', 'l'))
    text = ''.join((ch for ch in text if not unicodedata.combining(ch)))
    return ' '.join(text.split())

def _rdzenie(text: str) -> list[str]:
    seen: list[str] = []
    for word in re.findall('[^\\W_]+', _zloz(text)):
        if len(word) >= 4 and word[:RDZEN] not in seen:
            seen.append(word[:RDZEN])
    return seen

def _czas(created_at: str) -> datetime:
    moment = datetime.fromisoformat(created_at)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment

def _dzien(value: Optional[str], koniec: bool) -> Optional[str]:
    if not value:
        return None
    day = date.fromisoformat(value)
    if koniec:
        day += timedelta(days=1)
    local = datetime(day.year, day.month, day.day, tzinfo=LONDYN)
    return local.astimezone(timezone.utc).isoformat(timespec='microseconds')

def _wypowiedzi(con: sqlite3.Connection, od: Optional[str], do: Optional[str]) -> dict[str, Any]:
    sql = "SELECT id, created_at, sender, recipient, payload_json FROM capsules WHERE thread_id=? AND type='chat_message'"
    params: list[Any] = [WATEK]
    if od:
        params.append(od)
        sql += ' AND created_at >= ?'
    if do:
        params.append(do)
        sql += ' AND created_at < ?'
    rows = con.execute(sql + ' ORDER BY created_at ASC, id ASC', params).fetchall()
    stol: list[dict[str, Any]] = []
    kopie: list[dict[str, Any]] = []
    poza: list[dict[str, Any]] = []
    wadliwe = 0
    for row in rows:
        try:
            payload = json.loads(row['payload_json'])
        except (ValueError, TypeError):
            continue
        text = payload.get('text') if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            moment = _czas(row['created_at'])
        except (ValueError, TypeError):
            wadliwe += 1
            continue
        audience = payload.get('recipient')
        item = {'id': row['id'], 'autor': row['sender'], 'tekst': text, 'kopie': [], '_moment': moment, '_zlozony': _zloz(text), '_rdzenie': frozenset(_rdzenie(text))}
        if row['recipient'] in KANALY:
            stol.append({**item, 'kanal': row['recipient']})
        elif audience in KANALY:
            kopie.append({**item, 'kanal': audience})
        else:
            poza.append(item)
    wedlug_tresci: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in stol:
        wedlug_tresci.setdefault((item['autor'], item['tekst'], item['kanal']), []).append(item)
    sieroty = []
    for kopia in kopie:
        kandydaci = wedlug_tresci.get((kopia['autor'], kopia['tekst'], kopia['kanal']), [])
        najblizszy = min(kandydaci, key=lambda o: abs(o['_moment'] - kopia['_moment']), default=None)
        if najblizszy is not None and abs(najblizszy['_moment'] - kopia['_moment']) <= KOPIA_W_CIAGU:
            najblizszy['kopie'].append(kopia['id'])
        else:
            sieroty.append(kopia)
    return {'stol': stol, 'poza': poza, 'sieroty': sieroty, 'wadliwe': wadliwe}

def _trafia(item: dict[str, Any], fraza: str, rdzenie: list[str]) -> Optional[tuple[int, str]]:
    if fraza and fraza in item['_zlozony']:
        return (0, 'fraza')
    if rdzenie and all((r in item['_rdzenie'] for r in rdzenie)):
        return (1, f'słowa {len(rdzenie)}/{len(rdzenie)}')
    return None

def _czesciowe(wszystkie: list[dict[str, Any]], kandydaci: list[int], rdzenie: list[str]) -> list[tuple[float, int, int]]:
    if len(rdzenie) <= MIN_WSPOLNYCH or not wszystkie:
        return []
    n = len(wszystkie)
    srednia = sum((len(i['_rdzenie']) for i in wszystkie)) / n or 1.0
    idf = {}
    for r in rdzenie:
        df = sum((1 for i in wszystkie if r in i['_rdzenie']))
        idf[r] = math.log(1 + (n - df + 0.5) / (df + 0.5))
    out = []
    for index in kandydaci:
        item = wszystkie[index]
        wspolne = [r for r in rdzenie if r in item['_rdzenie']]
        if len(wspolne) >= MIN_WSPOLNYCH:
            dlugosc = 1 + BM25_K1 * (1 - BM25_B + BM25_B * len(item['_rdzenie']) / srednia)
            out.append((sum((idf[r] for r in wspolne)) * (BM25_K1 + 1) / dlugosc, index, len(wspolne)))
    return out

def _pokaz(item: dict[str, Any]) -> dict[str, Any]:
    local = item['_moment'].astimezone(LONDYN)
    text = item['tekst']
    return {'id': item['id'], 'autor': item['autor'], 'kanal': item['kanal'], 'czas': local.strftime('%Y-%m-%d %H:%M:%S %Z'), 'tekst': text if len(text) <= MAX_TEKST else text[:MAX_TEKST].rstrip() + '…', 'uciete': len(text) > MAX_TEKST, 'kopie': list(item['kopie'])}

def _zajetosc_xian() -> str:
    try:
        from xian.chat_busy import is_chat_busy
        return 'liczy' if is_chat_busy() else 'wolna'
    except Exception:
        return 'nieustalona'

def _xian_liczy() -> bool:
    return _zajetosc_xian() != 'wolna'

class _BladRecall(RuntimeError):
    pass
_PYTAJACY: dict[str, str] = {}

def _srodowisko_recall() -> dict[str, str]:
    srodowisko = {k: v for k, v in os.environ.items() if k not in ('LIRA_KTO', 'LIRA_PROBA')}
    return {**srodowisko, **_PYTAJACY}

def _recall_polecenie(query: str, top: int) -> dict[str, Any]:
    done = subprocess.run([sys.executable, '-m', 'agents.memory.recall', query, '--top-k', str(top), '--json'], cwd=APP_ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180, env=_srodowisko_recall())
    if done.returncode != 0:
        raise _BladRecall(f'blad_procesu: kod {done.returncode}')
    out = done.stdout.strip()
    if out.startswith('Brak trafien'):
        return {'stan': 'ok', 'wyniki': []}
    if out.startswith('Model mowi'):
        return {'stan': 'xian_liczy', 'wyniki': []}
    try:
        data = json.loads(out)
    except ValueError as exc:
        raise _BladRecall('blad_formatu') from exc
    results = data.get('results') if isinstance(data, dict) else data
    if not isinstance(results, list) or not all((isinstance(r, dict) for r in results)):
        raise _BladRecall('blad_formatu')
    wynik: dict[str, Any] = {'stan': 'ok', 'wyniki': results}
    if isinstance(data, dict) and data.get('retrieval_id'):
        wynik['retrieval_id'] = data['retrieval_id']
    return wynik

def _recall(query: str, top: int) -> dict[str, Any]:
    if _xian_liczy():
        stan = 'zajetosc_nieustalona' if _zajetosc_xian() == 'nieustalona' else 'xian_liczy'
        return {'stan': stan, 'wyniki': []}
    try:
        odpowiedz = _recall_polecenie(query, top)
    except _BladRecall as exc:
        return {'stan': str(exc), 'wyniki': []}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'stan': f'blad_procesu: {type(exc).__name__}', 'wyniki': []}
    return odpowiedz if isinstance(odpowiedz, dict) else {'stan': 'ok', 'wyniki': list(odpowiedz)}

def szukaj(query: str, *, db: Optional[Path]=None, autor: Optional[str]=None, kanal: Optional[str]=None, od: Optional[str]=None, do: Optional[str]=None, top: int=5, kontekst: int=2, znaczenie: str='auto', kolejnosc: str='najstarsze', czesciowe: bool=True) -> dict[str, Any]:
    query = ' '.join(str(query).split())[:MAX_ZAPYTANIE]
    top = max(1, min(int(top), MAX_TOP))
    kontekst = max(0, min(int(kontekst), MAX_KONTEKST))
    con = _polacz(Path(db) if db else _domyslna_baza())
    try:
        odczyt = _wypowiedzi(con, _dzien(od, False), _dzien(do, True))
    except sqlite3.Error as exc:
        raise BrakSzyny(f'szyna nieczytelna: {exc}') from exc
    finally:
        con.close()
    wszystkie = odczyt['stol']
    kierunek = -1.0 if kolejnosc == 'najnowsze' else 1.0
    fraza, rdzenie = (_zloz(query), _rdzenie(query))
    trafienia: list[tuple[tuple, int, str]] = []
    reszta: list[int] = []
    for index, item in enumerate(wszystkie):
        if autor and item['autor'] != autor:
            continue
        if kanal and item['kanal'] != kanal:
            continue
        trafione = _trafia(item, fraza, rdzenie)
        if trafione:
            trafienia.append(((trafione[0], kierunek * item['_moment'].timestamp()), index, trafione[1]))
        else:
            reszta.append(index)
    trafienia.sort()
    niepelne = sorted(_czesciowe(wszystkie, reszta, rdzenie) if czesciowe else [], key=lambda t: (-t[0], kierunek * wszystkie[t[1]]['_moment'].timestamp()))

    def _sasiedzi(index: int, krok: int) -> list[dict[str, Any]]:
        item, found, j = (wszystkie[index], [], index + krok)
        while 0 <= j < len(wszystkie) and len(found) < kontekst and (abs(wszystkie[j]['_moment'] - item['_moment']) <= SASIEDZTWO):
            if not kanal or wszystkie[j]['kanal'] == kanal:
                found.append(_pokaz(wszystkie[j]))
            j += krok
        return found[::-1] if krok < 0 else found
    slowa = [{**_pokaz(wszystkie[index]), 'dopasowanie': dopasowanie, 'przed': _sasiedzi(index, -1), 'po': _sasiedzi(index, 1)} for _, index, dopasowanie in trafienia[:top]]
    czesc = [{**_pokaz(wszystkie[index]), 'dopasowanie': f'część słów {ile}/{len(rdzenie)}', 'waga': round(waga, 2), 'przed': _sasiedzi(index, -1), 'po': _sasiedzi(index, 1)} for waga, index, ile in niepelne[:max(0, top - len(slowa))]]

    def _ile(items: list[dict[str, Any]]) -> int:
        return sum((1 for i in items if (not autor or i['autor'] == autor) and _trafia(i, fraza, rdzenie)))
    wynik: dict[str, Any] = {'zapytanie': query, 'top': top, 'kontekst': kontekst, 'slowa': slowa, 'kolejnosc': 'najnowsze' if kierunek < 0 else 'najstarsze', 'wszystkich_trafien': len(trafienia), 'czesciowe': czesc, 'czesciowych_trafien': len(niepelne), 'poza_stolem': _ile(odczyt['poza']), 'kopie_bez_oryginalu': _ile(odczyt['sieroty']), 'wadliwe_wiersze': odczyt['wadliwe'], 'stan_slow': 'ok' if slowa else 'czesciowe' if czesc else 'brak_trafien', 'znaczenie': None, '_trafione': [wszystkie[index]['id'] for _, index, _ in trafienia]}
    if znaczenie == 'tak' or (znaczenie == 'auto' and (not slowa)):
        wynik['znaczenie'] = dict(_recall(query, top))
        wynik['znaczenie']['filtry_zastosowane'] = False if autor or kanal or od or do else None
    return wynik

def _runda(db: Optional[Path], warunek: str, parametry: tuple) -> list[dict[str, str]]:
    con = _polacz(Path(db) if db else _domyslna_baza())
    try:
        rows = con.execute(f"SELECT id, created_at, sender, payload_json FROM capsules WHERE thread_id=? AND type='chat_message' AND recipient IN ({','.join('?' * len(KANALY))}) AND sender IN ({','.join('?' * len(LUDZIE))}) {warunek} ORDER BY created_at DESC, id DESC LIMIT 2", (WATEK, *KANALY, *LUDZIE, *parametry)).fetchall()
    except sqlite3.Error as exc:
        raise BrakSzyny(f'szyna nieczytelna: {exc}') from exc
    finally:
        con.close()
    rundy = []
    for row in rows:
        try:
            tekst = str(json.loads(row['payload_json']).get('text') or '')
        except (ValueError, TypeError, AttributeError):
            tekst = ''
        rundy.append({'round_id': row['id'], 'ts': row['created_at'], 'autor': row['sender'], 'tekst': ' '.join(tekst.split())[:80]})
    return rundy

def biezace_miejsce(db: Optional[Path]=None) -> Optional[dict[str, str]]:
    rundy = _runda(db, '', ())
    return rundy[0] if rundy else None

def wskazana_runda(db: Optional[Path], wskazanie: str) -> dict[str, str]:
    wskazanie = str(wskazanie).strip()
    if len(wskazanie) < MIN_POCZATEK_RUNDY or any((c in wskazanie for c in '%_')):
        raise ValueError(f'podaj co najmniej {MIN_POCZATEK_RUNDY} znaków początku id rundy')
    rundy = _runda(db, 'AND id LIKE ?', (wskazanie + '%',))
    if len(rundy) != 1:
        raise ValueError('nie ma takiej rundy na stole' if not rundy else 'ten początek pasuje do kilku rund')
    return rundy[0]

def opisz_miejsce(miejsce: Optional[dict[str, str]]) -> str:
    if not miejsce:
        return 'bez rundy'
    try:
        kiedy = _czas(miejsce['ts']).astimezone(LONDYN).strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError, KeyError):
        kiedy = str(miejsce.get('ts', ''))[:16]
    kto = f' — {miejsce['autor']}: „{miejsce['tekst']}”' if miejsce.get('autor') else ''
    return f'runda {miejsce['round_id'][:8]} z {kiedy}{kto}'

def dolacz_powroty(wynik: dict[str, Any], dziennik: Optional[Path]=None) -> None:
    try:
        powroty = usage.returns(dziennik)
    except OSError:
        powroty = {}
    for trafienie in [*wynik['slowa'], *wynik.get('czesciowe', [])]:
        trafienie['powroty'] = powroty.get(f'capsule:{trafienie['id']}', [])

def _kapsuly(db: Optional[Path], ids: list[str]) -> dict[str, dict[str, Any]]:
    znalezione: dict[str, dict[str, Any]] = {}
    if not ids:
        return znalezione
    con = _polacz(Path(db) if db else _domyslna_baza())
    try:
        for start in range(0, len(ids), 400):
            paczka = ids[start:start + 400]
            rows = con.execute(f"SELECT id, created_at, sender, recipient, payload_json FROM capsules WHERE thread_id=? AND type='chat_message' AND id IN ({','.join('?' * len(paczka))})", (WATEK, *paczka)).fetchall()
            for row in rows:
                if row['recipient'] not in KANALY:
                    znalezione[row['id']] = {'poza_stolem': True}
                    continue
                try:
                    tekst = str(json.loads(row['payload_json']).get('text') or '')
                    czas = _czas(row['created_at']).astimezone(LONDYN).strftime('%Y-%m-%d %H:%M')
                except (ValueError, TypeError, AttributeError):
                    continue
                znalezione[row['id']] = {'czas': czas, 'autor': row['sender'], 'kanal': row['recipient'], 'tekst': ' '.join(tekst.split())}
    except sqlite3.Error as exc:
        raise BrakSzyny(f'szyna nieczytelna: {exc}') from exc
    finally:
        con.close()
    return znalezione

def _moment_ogniwa(ogniwo: dict[str, Any]) -> Optional[datetime]:
    try:
        return _czas(ogniwo['ts'])
    except (ValueError, TypeError, KeyError):
        return None

def zbuduj_watek(wynik: dict[str, Any], dziennik: Optional[Path]=None, db: Optional[Path]=None, wstecz: int=0) -> Optional[dict[str, Any]]:
    try:
        powroty = usage.returns(dziennik)
    except OSError:
        return None
    pelne = {f'capsule:{i}' for i in wynik.get('_trafione', [])}
    trafione = set(pelne) | {f'capsule:{t['id']}' for t in wynik.get('czesciowe', [])}
    for r in (wynik.get('znaczenie') or {}).get('wyniki') or []:
        if isinstance(r, dict) and (r.get('capsule_id') or r.get('id')):
            trafione.add(f'capsule:{r['capsule_id']}' if r.get('capsule_id') else usage.source_key(str(r['id'])))
    ziarna = []
    for zrodlo, ogniwa in powroty.items():
        for ogniwo in ogniwa:
            if ogniwo['state'] in ZYWE_OGNIWA and _moment_ogniwa(ogniwo) is not None and (zrodlo in trafione):
                ziarna.append(ogniwo)
    if not ziarna:
        return None
    ziarna.sort(key=_moment_ogniwa)
    rozmowy: list[list[dict[str, Any]]] = []
    for ogniwo in ziarna:
        if rozmowy and _moment_ogniwa(ogniwo) - _moment_ogniwa(rozmowy[-1][-1]) <= PRZERWA_ROZMOWY:
            rozmowy[-1].append(ogniwo)
        else:
            rozmowy.append([ogniwo])
    rozmowy.reverse()

    def _rundy(rozmowa: list[dict[str, Any]]) -> list[str]:
        return list(dict.fromkeys((o['round_id'] for o in rozmowa if o['round_id'])))

    def _zrodel(rozmowa: list[dict[str, Any]]) -> int:
        rundy = set(_rundy(rozmowa))
        w_rundach = {z for z, ogniwa in powroty.items() for o in ogniwa if o['round_id'] in rundy and o['state'] in ZYWE_OGNIWA}
        return len(w_rundach | {o['source'] for o in rozmowa})

    def _zakres(rozmowa: list[dict[str, Any]]) -> tuple[str, str]:
        chwile = [_moment_ogniwa(o).astimezone(LONDYN) for o in rozmowa]
        return (min(chwile).strftime('%Y-%m-%d %H:%M'), max(chwile).strftime('%Y-%m-%d %H:%M'))
    watek: dict[str, Any] = {'rozmow': len(rozmowy), 'wstecz': wstecz, 'rundy': [], 'bez_rundy': [], 'starsze': []}
    if wstecz >= len(rozmowy):
        watek['brak'] = True
        return watek
    rozmowa = rozmowy[wstecz]
    watek['od'], watek['do'] = _zakres(rozmowa)
    watek['starsze'] = [{'od': _zakres(r)[0], 'zrodel': _zrodel(r), 'wstecz': wstecz + n} for n, r in enumerate(rozmowy[wstecz + 1:], 1)]
    rundy = _rundy(rozmowa)
    luzne = [o for o in rozmowa if not o['round_id']]
    do_pobrania = list(rundy)
    w_rundzie: dict[str, list[dict[str, Any]]] = {r: [] for r in rundy}
    for zrodlo, ogniwa in powroty.items():
        for ogniwo in ogniwa:
            if ogniwo['round_id'] in w_rundzie:
                w_rundzie[ogniwo['round_id']].append(ogniwo)
    for ogniwo in [o for lista in w_rundzie.values() for o in lista] + luzne:
        if ogniwo['source'].startswith('capsule:'):
            do_pobrania.append(ogniwo['source'].split(':', 1)[1])
    kapsuly = _kapsuly(db, list(dict.fromkeys(do_pobrania)))

    def _wpis(ogniwo: dict[str, Any], dopisek: str='') -> dict[str, Any]:
        zrodlo = ogniwo['source']
        cid = zrodlo.split(':', 1)[1] if zrodlo.startswith('capsule:') else ''
        k = kapsuly.get(cid, {})
        tekst = k.get('tekst', '')
        return {'zrodlo': zrodlo, 'id': cid if k else zrodlo, 'czas': k.get('czas', ''), 'autor': k.get('autor', ''), 'kanal': k.get('kanal', ''), 'poza_stolem': bool(k.get('poza_stolem')), 'tekst': tekst if len(tekst) <= MAX_TEKST_WATKU else tekst[:MAX_TEKST_WATKU].rstrip() + '…', 'stan': ogniwo['state'], 'oceny': {kto: ocena + dopisek for kto, ocena in ogniwo['verdicts'].items()}, 'pytania': list(ogniwo['queries']), 'wnioski': dict(ogniwo.get('notes') or {}), 'bez_slowa': zrodlo not in pelne, 'powtorka': False}
    wpisy: dict[str, dict[str, Any]] = {}
    for runda in rundy:
        ogniwa = w_rundzie[runda]
        zrodla = sorted((_wpis(o) for o in ogniwa if o['state'] in ZYWE_OGNIWA), key=lambda w: (w['czas'] or '9999', w['zrodlo']))
        odrzucone = [o for o in ogniwa if o['state'] == 'nietrafne']
        naglowek = kapsuly.get(runda, {})
        watek['rundy'].append({'round_id': runda, 'czas': naglowek.get('czas', ''), 'autor': naglowek.get('autor', ''), 'tekst': naglowek.get('tekst', '')[:160], 'zrodla': zrodla, 'odrzucone': len(odrzucone), 'odrzucone_wnioski': list(dict.fromkeys((uwaga for o in odrzucone for uwaga in (o.get('notes') or {}).values() if uwaga)))})
        for wpis in zrodla:
            wpis['powtorka'] = wpis['zrodlo'] in wpisy
            wpisy.setdefault(wpis['zrodlo'], wpis)
    for ogniwo in luzne:
        if ogniwo['source'] in wpisy:
            cel = wpisy[ogniwo['source']]
            for kto, ocena in ogniwo['verdicts'].items():
                cel['oceny'].setdefault(kto, f'{ocena} (bez rundy)')
            for kto, uwaga in (ogniwo.get('notes') or {}).items():
                cel['wnioski'].setdefault(kto, uwaga)
            cel['pytania'] += [q for q in ogniwo['queries'] if q not in cel['pytania']]
        else:
            wpis = _wpis(ogniwo, ' (bez rundy)')
            wpisy[ogniwo['source']] = wpis
            watek['bez_rundy'].append(wpis)
    watek['zrodel'] = len(wpisy)
    return watek

def zapisz_slad(wynik: dict[str, Any], kto: str, dziennik: Optional[Path]=None, miejsce: Any=False, proba: bool=False) -> None:
    watek = wynik.get('watek') or {}
    wyniki = [{'id': w['zrodlo'], 'capsule_id': w['id'], 'source': 'stol_watek'} if w['zrodlo'].startswith('capsule:') else {'id': w['zrodlo'], 'source': 'stol_watek'} for w in [z for r in watek.get('rundy', []) for z in r['zrodla']] + watek.get('bez_rundy', [])]
    if wynik.get('surowe', True):
        wyniki += [{'id': f'capsule:{t['id']}', 'capsule_id': t['id'], 'source': zrodlo, 'score': t.get('waga')} for zrodlo, lista in (('stol_slowa', wynik['slowa']), ('stol_czesc', wynik.get('czesciowe', []))) for t in lista]
    wyniki += [dict(r) for r in (wynik.get('znaczenie') or {}).get('wyniki') or [] if isinstance(r, dict) and r.get('id')]
    wyniki = list({w['id']: w for w in reversed(wyniki)}.values())[::-1]
    try:
        wynik['slad'] = usage.record_retrieval(wynik['zapytanie'], wyniki, actor=kto or BEZ_IMIENIA, channel=KANAL_SLADU, place=miejsce, probe=proba, log_path=dziennik)['retrieval_id']
        wynik['slad_kto'] = kto
        wynik['slad_wynikow'] = len(wyniki)
        if miejsce is not False:
            wynik['miejsce'] = miejsce
    except (OSError, ValueError) as exc:
        wynik['slad_blad'] = f'{type(exc).__name__}: {exc}'[:200]

def opisz_powrot(ogniwo: dict[str, Any]) -> str:
    try:
        kiedy = _czas(ogniwo['ts']).astimezone(LONDYN).strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        kiedy = str(ogniwo.get('ts'))[:16]
    pytania = '; '.join((f'„{q[:60]}”' for q in ogniwo['queries'][:2]))
    oceny = ', '.join((f'{kto}: {ocena}' for kto, ocena in ogniwo['verdicts'].items()))
    stan = 'SPORNE' if ogniwo['state'] == 'sporne' else ogniwo['state']
    gdzie = f'w rundzie z {kiedy}' if ogniwo.get('round_id') else f'bez rundy, ocena z {kiedy}'
    return f'{stan} {gdzie} ({oceny})' + (f' — pytanie {pytania}' if pytania else '')
ZNAK_POWROTU = {'trafne': '↺', 'nietrafne': '✗', 'sporne': '≠', 'poprawione': '✎'}

def _render_watek(watek: dict[str, Any]) -> list[str]:
    if watek.get('brak'):
        return [f'Nie ma tak dawnej rozmowy na ten temat (są {watek['rozmow']}; --wstecz liczy od 0). Niżej surowe trafienia.']
    ile = watek['zrodel']
    ktora = 'OSTATNIA ROZMOWA NA TEN TEMAT' if not watek['wstecz'] else f'ROZMOWA O {watek['wstecz']} WSTECZ'
    zakres = watek['od'] if watek['od'] == watek['do'] else f'{watek['od']} – {watek['do'][11:]}'
    lines = [f'## {ktora} — {zakres}, rund: {len(watek['rundy'])}, oznaczonych źródeł: {ile}', 'Tylko źródła wtedy oznaczone. To współwystąpienie ocen w rundach, nie dowód jednego znaczenia — pytanie Operator nad każdą rundą mówi, czego dotyczyła.']
    numer = 0
    numery: dict[str, int] = {}

    def _zrodlo(w: dict[str, Any]) -> None:
        nonlocal numer
        if w['powtorka']:
            oceny = ', '.join((f'{kto}: {ocena}' for kto, ocena in w['oceny'].items()))
            wnioski = '; '.join((f'{kto}: {uwaga}' for kto, uwaga in w['wnioski'].items()))
            lines.append(f'  {ZNAK_POWROTU[w['stan']]} ponownie [{numery[w['zrodlo']]}] — {oceny}' + (f' — {wnioski}' if wnioski else ''))
            return
        numer += 1
        numery[w['zrodlo']] = numer
        if w.get('poza_stolem'):
            naglowek = f'wypowiedź poza stołem (adres osobisty) — niecytowana  id={w['id']}'
        elif w['czas']:
            naglowek = f'{w['czas']}  {w['autor']} → {w['kanal']}  id={w['id']}'
        else:
            naglowek = f'źródło spoza stołu  id={w['id']}'
        lines.append(f'  [{numer}] {ZNAK_POWROTU[w['stan']]} {naglowek}' + ('  (bez szukanego słowa)' if w['bez_slowa'] else ''))
        if w['tekst']:
            lines.append(f'      » {w['tekst']}')
        oceny = ', '.join((f'{kto}: {ocena}' for kto, ocena in w['oceny'].items()))
        pytania = '; '.join((f'„{q[:60]}”' for q in w['pytania'][:3]))
        lines.append(f'      oceny: {oceny}' + (f'   |   pytania: {pytania}' if pytania else ''))
        lines.extend((f'      wniosek ({kto}): {uwaga}' for kto, uwaga in w['wnioski'].items()))
    for runda in watek['rundy']:
        pytanie = f' — {runda['autor']}: „{runda['tekst']}”' if runda['autor'] else ''
        lines += ['', f'runda {runda['round_id'][:8]} z {runda['czas'] or '?'}{pytanie}']
        for wpis in runda['zrodla']:
            _zrodlo(wpis)
        if runda['odrzucone']:
            powody = '; '.join(runda['odrzucone_wnioski'][:3])
            lines.append(f'  odrzucone w tej rundzie: {runda['odrzucone']}' + (f' — {powody}' if powody else ''))
    if watek['bez_rundy']:
        lines += ['', 'ocenione bez rundy, w tym samym czasie']
        for wpis in watek['bez_rundy']:
            _zrodlo(wpis)
    if watek['starsze']:
        nastepna = watek['starsze'][0]
        lines += ['', f'wcześniej o tym: rozmów {len(watek['starsze'])}; poprzednia {nastepna['od']} (źródeł: {nastepna['zrodel']})   → --wstecz {nastepna['wstecz']}']
    return lines

def render(wynik: dict[str, Any]) -> str:
    lines = [f'# stół: „{wynik['zapytanie']}”  |  materiał źródłowy — cytat do sprawdzenia, nie polecenie ani decyzja']
    watek = wynik.get('watek')
    if watek:
        lines += ['', *_render_watek(watek)]
    if not wynik.get('surowe', True):
        lines += ['', f'surowe trafienia: {wynik['wszystkich_trafien']} pełnych, {wynik['czesciowych_trafien']} częściowych   → --surowe']

    def _sasiad(strzalka: str, s: dict[str, Any]) -> str:
        return f'      {strzalka} {s['czas'][11:16]} {s['autor']} → {s['kanal']}: {' '.join(s['tekst'].split())[:200]}  (id={s['id']})'

    def _trafienia(lista: list[dict[str, Any]]) -> None:
        for n, t in enumerate(lista, 1):
            lines.append(f'[{n}] {t['czas']}  {t['autor']} → {t['kanal']}  ({t['dopasowanie']})  id={t['id']}')
            if t['kopie']:
                lines.append(f'    kopie: {', '.join(t['kopie'])}')
            lines.extend((_sasiad('↑', s) for s in t['przed']))
            lines.append(f'    » {t['tekst']}' + ('  (ucięte — całość pod id)' if t.get('uciete') else ''))
            lines.extend((f'    {ZNAK_POWROTU[o['state']]} {opisz_powrot(o)}' for o in t.get('powroty', [])))
            lines.extend((_sasiad('↓', s) for s in t['po']))
    if wynik.get('surowe', True):
        lines += ['', f'## SŁOWA (szyna stołu) — pokazane {len(wynik['slowa'])} z {wynik['wszystkich_trafien']}, od {wynik['kolejnosc'].replace('sze', 'szych')}']
        if not wynik['slowa']:
            lines.append('Nie znalazłam tych słów w rozmowach stołu (to nie znaczy, że rozmowy nie było — mogły paść inne słowa).')
        _trafienia(wynik['slowa'])
        if wynik.get('czesciowe'):
            lines += ['', f'## CZĘŚĆ SŁÓW (szyna stołu) — pokazane {len(wynik['czesciowe'])} z {wynik['czesciowych_trafien']}, od najlepiej pasujących; to kandydaci, mogą być nie na temat']
            _trafienia(wynik['czesciowe'])
    if wynik.get('poza_stolem'):
        lines.append(f'Trafienia poza stołem (adresy osobiste): {wynik['poza_stolem']} — tu niecytowane.')
    if wynik.get('kopie_bez_oryginalu'):
        lines.append(f'Kopie do skrzynek bez oryginału w tym zakresie: {wynik['kopie_bez_oryginalu']} — to nie źródło, poszerz daty.')
    if wynik.get('wadliwe_wiersze'):
        lines.append(f'Pominięte wiersze z nieczytelnym czasem zapisu: {wynik['wadliwe_wiersze']}.')
    z = wynik.get('znaczenie')
    if z is not None:
        lines += ['', '## ZNACZENIE (recall — inna metoda, wyników nie porównuj liczbami ze słowami)']
        if z.get('filtry_zastosowane') is False:
            lines.append('UWAGA: filtry autora, kanału i dat NIE obowiązują tej części — recall przeszukał całą pamięć.')
        if z['stan'] == 'xian_liczy':
            lines.append('Model liczy — recall teraz nie odpowie. Spróbuj za chwilę, nie w pętli.')
        elif z['stan'] == 'zajetosc_nieustalona':
            lines.append('Nie udało się ustalić, czy Model liczy — recall nie został uruchomiony.')
        elif z['stan'] != 'ok':
            lines.append(f'Recall niedostępny ({z['stan']}) — to nie znaczy, że pamięć jest pusta.')
        elif not z['wyniki']:
            lines.append('Recall odpowiedział poprawnie i nic nie znalazł.')
        if z.get('retrieval_id'):
            lines.append(f'ślad wyszukania: {z['retrieval_id']}')
        for n, r in enumerate(z['wyniki'], 1):
            text = ' '.join(str(r.get('text') or r.get('snippet') or '').split())[:300]
            skad = r.get('kind') or r.get('source') or '?'
            kto = ' '.join((str(r[k]) for k in ('sender', 'author', 'ts', 'created_at') if r.get(k)))
            lines.append(f'[{n}] {skad}  id={r.get('id', '?')}' + (f'  ({kto})' if kto else '') + f'\n    {text}')
    if wynik.get('slad'):
        kto = wynik.get('slad_kto')
        komu = f'--kto {kto}' if kto else f'--kto <imię> --slad {wynik['slad']}'
        lines += ['', f'ślad wyszukania: {wynik['slad']}']
        if 'miejsce' in wynik:
            lines.append(f'miejsce rozmowy: {opisz_miejsce(wynik['miejsce'])}' + ('   (inna: --runda <początek id>, żadna: --bez-rundy)' if wynik['miejsce'] else ''))
        if wynik.get('slad_wynikow'):
            lines.append(f'pokwituj: python3 -m tools.trafienie tak <początek id> {komu}   |   python3 -m tools.trafienie nie {komu}')
    elif wynik.get('slad_blad'):
        lines += ['', f'ślad niezapisany ({wynik['slad_blad']}) — wynik jest pełny, ale nie będzie czego pokwitować.']
    return '\n'.join(lines)

def main(argv: Optional[list[str]]=None) -> int:
    ap = argparse.ArgumentParser(prog='stol_szukaj', description='Słowa na szynie stołu, potem recall po znaczeniu')
    ap.add_argument('zapytanie', nargs='+')
    ap.add_argument('--autor', help='np. Operator, Lira, Reviewer, Model')
    ap.add_argument('--kanal', choices=KANALY, help='domyślnie oba kanały stołu')
    ap.add_argument('--od', help='RRRR-MM-DD, dzień w UTC, włącznie')
    ap.add_argument('--do', help='RRRR-MM-DD, dzień w UTC, włącznie')
    ap.add_argument('--top', type=int, default=5)
    ap.add_argument('--kontekst', type=int, default=2, help='ile sąsiednich wypowiedzi przed i po')
    ap.add_argument('--znaczenie', choices=('auto', 'tak', 'nie'), default='auto', help='auto: recall tylko gdy słowa nic nie dały')
    ap.add_argument('--kolejnosc', choices=('najstarsze', 'najnowsze'), default='najstarsze', help='najstarsze: źródło przed cytatami; najnowsze: ostatni stan sprawy')
    ap.add_argument('--bez-czesciowych', action='store_true', help='tylko fraza i komplet słów zapytania')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--kto', default=os.environ.get('LIRA_KTO', ''), help='kto pyta (Reviewer, Lira…) — pod tym imieniem kwitujesz potem trafienie')
    ap.add_argument('--proba', action='store_true', default=os.environ.get('LIRA_PROBA') == '1', help='szukanie próbne (kontrola, pomiar narzędzia) — ślad dostaje znak próby')
    ap.add_argument('--bez-sladu', action='store_true', help='nie dopisuj wyszukania do dziennika')
    ap.add_argument('--runda', help='początek id wypowiedzi Operator, której dotyczy to szukanie (domyślnie ostatnia)')
    ap.add_argument('--bez-rundy', action='store_true', help='szukanie nie dotyczy żadnej rundy stołu (np. nocna robota)')
    ap.add_argument('--surowe', action='store_true', help='pokaż też surowe trafienia, gdy jest rozmowa, do której szukanie cofa')
    ap.add_argument('--wstecz', type=int, default=0, metavar='N', help='cofnij się o N rozmów na ten temat (0 = ostatnia)')
    ap.add_argument('--db', type=Path, help='inna baza (testy)')
    ap.add_argument('--dziennik', type=Path, help='inny dziennik wyszukań (testy)')
    args = ap.parse_args(argv)
    _PYTAJACY.clear()
    if args.kto.strip():
        _PYTAJACY['LIRA_KTO'] = args.kto.strip()
    if args.proba:
        _PYTAJACY['LIRA_PROBA'] = '1'
    miejsce: Any = False
    if not args.bez_sladu:
        try:
            miejsce = None if args.bez_rundy else wskazana_runda(args.db, args.runda) if args.runda else biezace_miejsce(args.db)
        except BrakSzyny as exc:
            print(f'STAN: {exc}. To brak dostępu do zapisu, nie brak rozmowy.')
            return 2
        except ValueError as exc:
            print(f'STAN: runda nieustalona ({exc}). Podaj inną --runda albo --bez-rundy.')
            return 2
    try:
        wynik = szukaj(' '.join(args.zapytanie), db=args.db, autor=args.autor, kanal=args.kanal, od=args.od, do=args.do, top=args.top, kontekst=args.kontekst, znaczenie=args.znaczenie, kolejnosc=args.kolejnosc, czesciowe=not args.bez_czesciowych)
    except BrakSzyny as exc:
        print(f'STAN: {exc}. To brak dostępu do zapisu, nie brak rozmowy.')
        return 2
    except ValueError as exc:
        print(f'STAN: złe dane wejściowe ({exc}).')
        return 2
    dolacz_powroty(wynik, args.dziennik)
    try:
        wynik['watek'] = zbuduj_watek(wynik, args.dziennik, args.db, max(0, args.wstecz))
    except BrakSzyny:
        wynik['watek'] = None
    wynik['surowe'] = bool(args.surowe or not wynik['watek'] or wynik['watek'].get('brak'))
    if not args.bez_sladu:
        zapisz_slad(wynik, args.kto.strip(), args.dziennik, miejsce, proba=args.proba)
    wynik.pop('_trafione', None)
    print(json.dumps(wynik, ensure_ascii=False, indent=2) if args.json else render(wynik))
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
