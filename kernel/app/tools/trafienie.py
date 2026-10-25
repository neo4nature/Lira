from __future__ import annotations
import argparse
from pathlib import Path
from typing import Any, Optional
from agents.memory import retrieval_usage as usage
from tools import stol_szukaj as ss
MIN_POCZATEK = 6
biezace_miejsce = ss.biezace_miejsce
opisz_powrot = ss.opisz_powrot

def _zrodlo(wynik: dict[str, Any]) -> str:
    return wynik.get('source_id') or usage.source_key(wynik.get('record_id', ''))

def _ogon(identyfikator: str) -> str:
    return identyfikator.split(':', 1)[-1]

def _krotko(wynik: dict[str, Any]) -> str:
    return _ogon(wynik['record_id'])[:13]

def _wedlug_zrodla(wyniki: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grupy: dict[str, list[dict[str, Any]]] = {}
    for w in wyniki:
        grupy.setdefault(_zrodlo(w), []).append(w)
    return list(grupy.values())

def _wskaz(wyniki: list[dict[str, Any]], poczatki: list[str]) -> tuple[list[list[dict[str, Any]]], list[str]]:
    wybrane: list[list[dict[str, Any]]] = []
    bledy: list[str] = []
    for poczatek in poczatki:
        trafione = {_zrodlo(w) for w in wyniki if _ogon(w['record_id']).startswith(poczatek) or _ogon(_zrodlo(w)).startswith(poczatek)}
        zrodla = _wedlug_zrodla([w for w in wyniki if _zrodlo(w) in trafione])
        if len(poczatek) < MIN_POCZATEK and len(zrodla) != 1:
            bledy.append(f'„{poczatek}”: podaj co najmniej {MIN_POCZATEK} znaków początku id')
        elif not zrodla:
            bledy.append(f'„{poczatek}”: nie ma takiego wyniku w tym wyszukaniu')
        elif len(zrodla) > 1:
            bledy.append(f'„{poczatek}”: pasuje do {len(zrodla)} różnych źródeł, podaj dłuższy początek')
        elif zrodla[0] not in wybrane:
            wybrane.append(zrodla[0])
    return (wybrane, bledy)

def _ogniwo(wynik: dict[str, Any], slad_id: str, miejsce: Optional[dict[str, str]], dziennik: Optional[Path]) -> Optional[dict[str, Any]]:
    for ogniwo in usage.returns(dziennik).get(_zrodlo(wynik), []):
        if miejsce and ogniwo['round_id'] == miejsce['round_id']:
            return ogniwo
        if not miejsce and (not ogniwo['round_id']) and (slad_id in ogniwo['retrieval_ids']):
            return ogniwo
    return None

def _opis(ogniwo: dict[str, Any]) -> str:
    oceny = ', '.join((f'{kto}: {ocena}' for kto, ocena in ogniwo['verdicts'].items()))
    if ogniwo['state'] == 'sporne':
        return f'SPORNE ({oceny}) — obie oceny zostają; czeka na Operator'
    return f'{ogniwo['state']} ({oceny})'

def _juz_oceniony(slad_id: str, record_id: str, kto: str, wynik: str, miejsce: Optional[dict[str, str]], dziennik: Optional[Path]) -> bool:
    ostatnia = None
    for e in usage.read_events(dziennik):
        if e.get('event_type') == 'outcome' and e.get('retrieval_id') == slad_id and (e.get('record_id') == record_id) and (e.get('actor') == kto):
            ostatnia = e
    return ostatnia is not None and ostatnia.get('outcome') == wynik and (((ostatnia.get('place') or {}).get('round_id') or '') == ((miejsce or {}).get('round_id') or ''))

def _miejsce(slad: dict[str, Any], runda: Optional[str], bez_rundy: bool, db: Optional[Path]) -> tuple[Optional[dict[str, str]], str]:
    if bez_rundy:
        return (None, 'bez rundy (na życzenie)')
    if runda:
        miejsce = ss.wskazana_runda(db, runda)
        return (miejsce, f'{ss.opisz_miejsce(miejsce)} (wskazana)')
    if 'place' in slad:
        miejsce = slad['place']
        return (miejsce, f'runda {miejsce['round_id'][:8]}, w której szukano' if miejsce else 'bez rundy (tak zapisano przy szukaniu)')
    miejsce = biezace_miejsce(db)
    if not miejsce:
        return (None, 'bez rundy — na stole nie ma wypowiedzi człowieka')
    return (miejsce, f'{ss.opisz_miejsce(miejsce)} — przyjęta domyślnie, bo ślad wyszukania nie niesie rundy; inna: --runda <początek id>, żadna: --bez-rundy')

def kwituj(ocena: str, poczatki: list[str], *, kto: str, slad_id: Optional[str]=None, uwaga: str='', runda: Optional[str]=None, bez_rundy: bool=False, db: Optional[Path]=None, dziennik: Optional[Path]=None) -> tuple[int, list[str]]:
    slad = usage.find_retrieval(slad_id, dziennik) if slad_id else usage.last_retrieval(kto, dziennik)
    if slad is None:
        return (2, [f'STAN: nie ma wyszukania o śladzie {slad_id}.' if slad_id else f'STAN: nie widzę wyszukania zapisanego jako {kto}. Podaj --slad ret_… z wyniku wyszukania (recall i wyszukania bez --kto mają własny ślad).'])
    wyniki = [r for r in slad.get('results', []) if isinstance(r, dict) and r.get('record_id')]
    do_wyboru = 'do wyboru: ' + (', '.join((_krotko(r) for r in wyniki)) or 'to wyszukanie nic nie zwróciło')
    if ocena == 'tak' and (not poczatki):
        return (2, ['STAN: trafne trzeba nazwać — podaj początek id źródła.', do_wyboru])
    wybrane, bledy = _wskaz(wyniki, poczatki) if poczatki else (_wedlug_zrodla(wyniki), [])
    if bledy:
        return (2, ['STAN: nic nie zapisałam.', *bledy, do_wyboru])
    if not wybrane:
        return (2, ['STAN: to wyszukanie nic nie zwróciło, nie ma czego oceniać.'])
    try:
        miejsce, skad = _miejsce(slad, runda, bez_rundy, db)
    except ValueError as exc:
        return (2, [f'STAN: nic nie zapisałam — {exc}.'])
    wynik = 'used' if ocena == 'tak' else 'rejected'
    lines = [f'wyszukanie {slad['retrieval_id']} („{slad.get('query', '')[:80]}”)', f'miejsce: {skad}']
    for rekordy in wybrane:
        pierwszy = rekordy[0]
        nowe = [r for r in rekordy if not _juz_oceniony(slad['retrieval_id'], r['record_id'], kto, wynik, miejsce, dziennik)]
        for rekord in nowe:
            usage.record_outcome(slad['retrieval_id'], rekord['record_id'], wynik, actor=kto, note=uwaga, place=miejsce, log_path=dziennik)
        ogniwo = _ogniwo(pierwszy, slad['retrieval_id'], miejsce, dziennik)
        if not nowe:
            lines.append(f'  {_krotko(pierwszy)}: już pokwitowane' + (f' — {_opis(ogniwo)}' if ogniwo else ''))
        elif ogniwo is None:
            lines.append(f'  {_krotko(pierwszy)}: zapisane jako {usage.VERDICTS[wynik]}')
        else:
            wczesniej = len(usage.chain(_zrodlo(pierwszy), dziennik)) - (1 if ogniwo['state'] == 'trafne' else 0)
            lines.append(f'  {_krotko(pierwszy)}: {_opis(ogniwo)}' + (f'; wcześniejszych trafnych powrotów do tego źródła: {wczesniej}' if wczesniej > 0 else ''))
    return (0, lines)

def _czas(ts: str) -> str:
    try:
        return ss._czas(ts).astimezone(ss.LONDYN).strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return str(ts)[:16]

def main(argv: Optional[list[str]]=None) -> int:
    ap = argparse.ArgumentParser(prog='trafienie', description='Pokwitowanie wyszukania: trafne albo nie')
    ap.add_argument('ocena', nargs='?', choices=('tak', 'nie'))
    ap.add_argument('zrodla', nargs='*', help='początki id z wyniku wyszukania (co najmniej 6 znaków)')
    ap.add_argument('--kto', help='Reviewer, Lira… — ta sama nazwa co przy szukaniu')
    ap.add_argument('--slad', help='ślad wyszukania (ret_…), gdy nie chodzi o własne ostatnie')
    ap.add_argument('--runda', help='początek id wypowiedzi Operator, do której należy ta ocena')
    ap.add_argument('--bez-rundy', action='store_true', help='ocena bez miejsca rozmowy')
    ap.add_argument('--uwaga', default='')
    ap.add_argument('--sporne', action='store_true', help='pokaż ogniwa z rozbieżnymi ocenami (czekają na Operator)')
    ap.add_argument('--niesprawdzone', action='store_true', help='pokaż własne wyszukania, których sama nie oceniłaś')
    ap.add_argument('--lancuch', metavar='ID', help='pokaż ocenione powroty do jednego źródła')
    ap.add_argument('--db', type=Path, help='inna baza stołu (testy)')
    ap.add_argument('--dziennik', type=Path, help='inny dziennik wyszukań (testy)')
    args = ap.parse_args(argv)
    if args.sporne:
        sporne = usage.disputed(args.dziennik)
        print('\n'.join((f'{o['source']}  {opisz_powrot(o)}' for o in sporne)) or 'Nie ma ogniw spornych.')
        return 0
    if args.lancuch:
        klucz = args.lancuch if ':' in args.lancuch else f'capsule:{args.lancuch}'
        ogniwa = usage.returns(args.dziennik).get(usage.source_key(klucz), [])
        print('\n'.join((opisz_powrot(o) for o in ogniwa)) or 'Do tego źródła nikt jeszcze nie wracał z oceną.')
        return 0
    if not args.kto:
        ap.error('--kto jest wymagane')
    if args.niesprawdzone:
        zdarzenia = usage.read_events(args.dziennik)
        ocenione = {e['retrieval_id'] for e in zdarzenia if e.get('event_type') == 'outcome' and e.get('actor') == args.kto}
        moje = [e for e in zdarzenia if e.get('event_type') == 'retrieval' and e.get('actor') == args.kto and (e['retrieval_id'] not in ocenione) and e.get('results')]
        print('\n'.join((f'{_czas(e['ts'])}  {e['retrieval_id']}  „{e.get('query', '')[:80]}”  ({len(e['results'])} wyników)' for e in moje[-20:])) or 'Nie ma nieocenionych wyszukań.')
        return 0
    if not args.ocena:
        ap.error('podaj ocenę: tak albo nie')
    if args.runda and args.bez_rundy:
        ap.error('--runda i --bez-rundy wykluczają się')
    try:
        kod, lines = kwituj(args.ocena, args.zrodla, kto=args.kto, slad_id=args.slad, uwaga=args.uwaga, runda=args.runda, bez_rundy=args.bez_rundy, db=args.db, dziennik=args.dziennik)
    except ss.BrakSzyny as exc:
        print(f'STAN: {exc}. Nic nie zapisałam.')
        return 2
    print('\n'.join(lines))
    return kod
if __name__ == '__main__':
    raise SystemExit(main())
