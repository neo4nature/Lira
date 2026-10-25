from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from config import memory_dir
NAZWA_PRZYSTAWKI = 'index_freshness.json'
SCHEMAT = 'lira.index_freshness.v1'
DLUGOSC_PROBKI = 200
MIN_ZNAKOW_ALIASU = 40
MIN_PROBEK_ALIASU = 3
MIN_POKRYCIE_ALIASU = 0.9
_pamiec: Dict[str, Any] = {'mtime_ns': None, 'ids': frozenset(), 'logical_source_ids': {}}

def _sha256(tekst: str) -> str:
    return hashlib.sha256(tekst.encode('utf-8', errors='ignore')).hexdigest()

def sciezka_indeksu() -> Path:
    return memory_dir() / 'embeddings' / 'embeddings_index.jsonl'

def sciezka_przystawki() -> Path:
    return memory_dir() / 'embeddings' / NAZWA_PRZYSTAWKI

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\]]')
_DWUKROPEK = re.compile(r'\s*:\s*')
_LICZBY = re.compile(r'\[[-+0-9.eE,\s]*\]')

def _bez_wektora(linia: str) -> str:
    """Skip a top-level numeric vector without discarding neighboring JSON fields.

    Strings and brackets are tokenized by the regex engine, so numbers are never
    walked character by character. Anything unusual returns the line unchanged."""
    depth = 0
    for m in _TOKEN.finditer(linia):
        t = m.group()
        if t[0] == '"':
            if depth == 1 and t == '"vector"':
                c = _DWUKROPEK.match(linia, m.end())
                if c:
                    v = _LICZBY.match(linia, c.end())
                    return linia[:v.start()] + '[]' + linia[v.end():] if v else linia
        elif t in '{[':
            depth += 1
        else:
            depth -= 1
    return linia

def _czytaj_plik(sciezka: str, bufor: Dict[str, Optional[str]]) -> Optional[str]:
    if sciezka not in bufor:
        try:
            bufor[sciezka] = Path(sciezka).read_text(encoding='utf-8', errors='replace')
        except Exception:
            bufor[sciezka] = None
    return bufor[sciezka]

def _znormalizuj(tekst: str) -> str:
    return ' '.join(tekst.split())

def _tresc_znormalizowana(sciezka: str, tresc: str, bufor: Dict[str, Optional[str]]) -> str:
    klucz = '\x00norm\x00' + sciezka
    if klucz not in bufor:
        bufor[klucz] = _znormalizuj(tresc)
    return bufor[klucz] or ''

def _ulotna_sciezka(sciezka: str) -> bool:
    return '/shared_context/events/' in sciezka.replace('\\', '/')

def _probka_aliasu(tekst: str) -> str:
    probka = _znormalizuj((tekst or '').strip())[:DLUGOSC_PROBKI]
    return probka if len(probka) >= MIN_ZNAKOW_ALIASU else ''

def _wnioskuj_aliasy(brakujace: Dict[str, List[str]], zywe_sciezki: Set[str], bufor: Dict[str, Optional[str]]) -> Tuple[Dict[str, str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    aliasy: Dict[str, str] = {}
    dowody: List[Dict[str, Any]] = []
    konflikty: List[Dict[str, Any]] = []
    tresci_zywych: Dict[str, str] = {}
    for stara, surowe_probki in sorted(brakujace.items()):
        probki = sorted({p for p in surowe_probki if p})
        if len(probki) < MIN_PROBEK_ALIASU:
            continue
        kandydaci: List[Tuple[int, float, str]] = []
        for zywa in sorted(zywe_sciezki):
            if zywa not in tresci_zywych:
                tresc = _czytaj_plik(zywa, bufor)
                tresci_zywych[zywa] = _znormalizuj(tresc or '')
            tresc = tresci_zywych[zywa]
            if not tresc:
                continue
            trafienia = sum((1 for probka in probki if probka in tresc))
            pokrycie = trafienia / len(probki)
            if trafienia >= MIN_PROBEK_ALIASU and pokrycie >= MIN_POKRYCIE_ALIASU:
                kandydaci.append((trafienia, pokrycie, zywa))
        kandydaci.sort(key=lambda x: (-x[0], -x[1], x[2]))
        if not kandydaci:
            continue
        if len(kandydaci) != 1:
            konflikty.append({'old_path': stara, 'reason': 'ambiguous_content_overlap', 'candidates': [{'path': p, 'matched_samples': h, 'coverage': round(c, 5)} for h, c, p in kandydaci[:5]]})
            continue
        trafienia, pokrycie, zywa = kandydaci[0]
        aliasy[stara] = zywa
        dowody.append({'old_path': stara, 'current_path': zywa, 'matched_samples': trafienia, 'samples': len(probki), 'coverage': round(pokrycie, 5), 'method': 'normalized_content_overlap'})
    return (aliasy, dowody, konflikty)

def _mapa_logicznych_zrodel(pierwsze_wystapienie: Dict[str, str], jawne_id: Dict[str, str], aliasy: Dict[str, str]) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    rodzic = {p: p for p in pierwsze_wystapienie}

    def znajdz(p: str) -> str:
        rodzic.setdefault(p, p)
        while rodzic[p] != p:
            rodzic[p] = rodzic[rodzic[p]]
            p = rodzic[p]
        return p

    def polacz(a: str, b: str) -> None:
        ra, rb = (znajdz(a), znajdz(b))
        if ra != rb:
            rodzic[rb] = ra
    for stara, nowa in aliasy.items():
        polacz(stara, nowa)
    grupy: Dict[str, List[str]] = {}
    for sciezka in rodzic:
        grupy.setdefault(znajdz(sciezka), []).append(sciezka)
    wynik: Dict[str, str] = {}
    konflikty: List[Dict[str, Any]] = []
    for sciezki in grupy.values():
        zadane = sorted({jawne_id[p] for p in sciezki if p in jawne_id})
        if len(zadane) > 1:
            konflikty.append({'reason': 'conflicting_explicit_logical_source_ids', 'paths': sorted(sciezki), 'logical_source_ids': zadane})
        if zadane:
            logical_id = zadane[0]
        else:
            kotwica = min(sciezki, key=lambda p: (pierwsze_wystapienie.get(p, '9999'), p))
            logical_id = 'file:' + _sha256(kotwica)[:24]
        for sciezka in sciezki:
            wynik[sciezka] = logical_id
    return (wynik, konflikty)

def _powod_nieswiezosci(rec: Dict[str, Any], bufor: Dict[str, Optional[str]], skroty: Dict[str, str]) -> Optional[str]:
    src = rec.get('source') or {}
    if src.get('kind') != 'file':
        return None
    sciezka = src.get('path')
    if not sciezka:
        return None
    if not os.path.exists(sciezka):
        return 'zrodlo_zniknelo'
    tresc = _czytaj_plik(sciezka, bufor)
    if tresc is None:
        return None
    meta = rec.get('meta') or {}
    skrot_pliku = meta.get('file_sha256')
    if skrot_pliku:
        if sciezka not in skroty:
            skroty[sciezka] = _sha256(tresc)
        if skrot_pliku == skroty[sciezka]:
            return None
    probka = (rec.get('text') or '').strip()[:DLUGOSC_PROBKI]
    if not probka:
        return None
    if probka in tresc:
        return None
    if _znormalizuj(probka) in _tresc_znormalizowana(sciezka, tresc, bufor):
        return None
    return 'tresci_juz_nie_ma_w_zrodle'

def przebuduj(index_path: Optional[Path]=None) -> Dict[str, Any]:
    index_path = index_path or sciezka_indeksu()
    from agents.memory.source_registry import derived_artifact_reason, registry_manifest
    manifest_rejestru = registry_manifest(memory_dir())
    bufor: Dict[str, Optional[str]] = {}
    skroty: Dict[str, str] = {}
    stale: List[str] = []
    powody: Dict[str, int] = {}
    rekordow = 0
    plikowych = 0
    pierwsze_wystapienie: Dict[str, str] = {}
    jawne_id: Dict[str, str] = {}
    brakujace_probki: Dict[str, List[str]] = {}
    zywe_sciezki: Set[str] = set()
    if index_path.exists():
        with index_path.open('r', encoding='utf-8', errors='replace') as f:
            for linia in f:
                linia = linia.strip()
                if not linia:
                    continue
                try:
                    rec = json.loads(_bez_wektora(linia))
                except Exception:
                    continue
                if rec.get('schema') != 'lira.embedding_record.v1':
                    continue
                rekordow += 1
                src = rec.get('source') or {}
                if src.get('kind') == 'file':
                    plikowych += 1
                    sciezka = src.get('path')
                    if sciezka:
                        ts = str(rec.get('ts') or '9999')
                        if sciezka not in pierwsze_wystapienie or ts < pierwsze_wystapienie[sciezka]:
                            pierwsze_wystapienie[sciezka] = ts
                        if src.get('logical_source_id'):
                            jawne_id[sciezka] = str(src['logical_source_id'])
                        if os.path.exists(sciezka):
                            zywe_sciezki.add(sciezka)
                        elif not _ulotna_sciezka(sciezka):
                            probka = _probka_aliasu(rec.get('text') or '')
                            if probka:
                                lista = brakujace_probki.setdefault(sciezka, [])
                                if probka not in lista and len(lista) < 512:
                                    lista.append(probka)
                powod = None
                if src.get('kind') == 'file' and src.get('path'):
                    powod = derived_artifact_reason(Path(str(src['path'])))
                if not powod:
                    powod = _powod_nieswiezosci(rec, bufor, skroty)
                if powod:
                    rid = rec.get('id')
                    if rid:
                        stale.append(rid)
                        powody[powod] = powody.get(powod, 0) + 1
    aliasy, dowody_aliasow, konflikty_aliasow = _wnioskuj_aliasy(brakujace_probki, zywe_sciezki, bufor)
    logiczne_id, konflikty_id = _mapa_logicznych_zrodel(pierwsze_wystapienie, jawne_id, aliasy)
    for dowod in dowody_aliasow:
        dowod['logical_source_id'] = logiczne_id.get(dowod['current_path'])
    raport = {'schema': SCHEMAT, 'index_path': str(index_path), 'records_scanned': rekordow, 'file_records': plikowych, 'stale_count': len(stale), 'reasons': powody, 'stale_ids': stale, 'logical_source_ids': logiczne_id, 'logical_source_aliases': dowody_aliasow, 'logical_source_conflicts': konflikty_aliasow + konflikty_id, 'source_registry_version': manifest_rejestru['version'], 'source_registry_hash': manifest_rejestru['registry_hash']}
    cel = sciezka_przystawki()
    cel.parent.mkdir(parents=True, exist_ok=True)
    tmp = cel.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(raport, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, cel)
    _pamiec['mtime_ns'] = None
    return raport

def _wczytaj_przystawke() -> None:
    try:
        cel = sciezka_przystawki()
        mtime_ns = cel.stat().st_mtime_ns
    except Exception:
        _pamiec['mtime_ns'] = None
        _pamiec['ids'] = frozenset()
        _pamiec['logical_source_ids'] = {}
        return
    if _pamiec['mtime_ns'] == mtime_ns:
        return
    try:
        dane = json.loads(cel.read_text(encoding='utf-8'))
        ids = frozenset(dane.get('stale_ids') or [])
        logiczne = dict(dane.get('logical_source_ids') or {})
    except Exception:
        ids = frozenset()
        logiczne = {}
    _pamiec['mtime_ns'] = mtime_ns
    _pamiec['ids'] = ids
    _pamiec['logical_source_ids'] = logiczne

def stale_ids() -> Set[str]:
    if os.getenv('LIRA_MEMORY_INDEX_FRESHNESS', '1') != '1':
        return set()
    _wczytaj_przystawke()
    return set(_pamiec['ids'])

def logical_source_ids() -> Dict[str, str]:
    _wczytaj_przystawke()
    return dict(_pamiec['logical_source_ids'])

def main() -> int:
    raport = przebuduj()
    print(json.dumps({k: v for k, v in raport.items() if k != 'stale_ids'}, ensure_ascii=False, indent=1))
    print(f'zapisane w {sciezka_przystawki()}')
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
