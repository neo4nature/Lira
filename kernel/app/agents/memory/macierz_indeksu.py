from __future__ import annotations
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import numpy as np
log = logging.getLogger('memory.macierz_indeksu')
SCHEMAT = 'lira.embedding_record.v1'
BLAD_F32 = 0.0003
ZALEGLOSC_MAX = 32 * 2 ** 20
_POCZ_POJ = 1024

def wlaczona() -> bool:
    return os.getenv('LIRA_RECALL_MACIERZ', '1').strip().lower() in ('1', 'true', 'yes', 'on')

class _Bufor:

    def __init__(self, dtype, szer: Optional[int]=None):
        ksztalt = (_POCZ_POJ,) if szer is None else (_POCZ_POJ, szer)
        self.a = np.empty(ksztalt, dtype=dtype)
        self.n = 0

    def dopisz(self, w) -> int:
        if self.n == len(self.a):
            nowa = np.empty((int(len(self.a) * 1.5) + 1,) + self.a.shape[1:], dtype=self.a.dtype)
            nowa[:self.n] = self.a[:self.n]
            self.a = nowa
        self.a[self.n] = w
        self.n += 1
        return self.n - 1

    def widok(self):
        return self.a[:self.n]

class MacierzIndeksu:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gotowa = threading.Event()
        self._watek: Optional[threading.Thread] = None
        self._wyczysc(None)

    def gotowa(self, sciezka: Optional[Path]=None) -> bool:
        if not self._gotowa.is_set():
            return False
        if sciezka is None:
            return True
        try:
            st = sciezka.stat()
        except OSError:
            return False
        return sciezka == self.sciezka and st.st_ino == self.inode and (self.przeczytane <= st.st_size) and (st.st_size - self.przeczytane <= ZALEGLOSC_MAX)

    def rozgrzej_w_tle(self, sciezka: Path, przepusc: Callable[[Dict[str, Any]], bool]) -> None:
        if self.gotowa(sciezka) or (self._watek is not None and self._watek.is_alive()):
            return

        def buduj():
            try:
                with self._lock:
                    self.odswiez(sciezka, przepusc)
            except Exception as exc:
                log.warning('[macierz] rozgrzewka w tle padła: %s', exc)
        self._watek = threading.Thread(target=buduj, name='macierz-rozgrzewka', daemon=True)
        self._watek.start()

    def _wyczysc(self, sciezka: Optional[Path]) -> None:
        self.sciezka = sciezka
        self.inode: Optional[int] = None
        self.przeczytane = 0
        self.dim: Optional[int] = None
        self.wiersze: Optional[_Bufor] = None
        self.wiersz_rek = _Bufor(np.int32)
        self.offset_rek = _Bufor(np.int64)
        self.id_rek: List[Any] = []
        self.skrot_wiersz: Dict[bytes, int] = {}

    def _czytaj_od(self, sciezka: Path, od: int, przepusc: Callable[[Dict[str, Any]], bool]) -> int:
        nowe = 0
        with sciezka.open('rb') as f:
            f.seek(od)
            poz = od
            for linia in f:
                if not linia.endswith(b'\n'):
                    break
                start, poz = (poz, poz + len(linia))
                tekst = linia.decode('utf-8', errors='ignore').strip()
                if not tekst:
                    continue
                try:
                    obj = json.loads(tekst)
                except Exception:
                    continue
                if not isinstance(obj, dict) or obj.get('schema') != SCHEMAT or (not przepusc(obj)):
                    continue
                vec = obj.get('vector')
                if not isinstance(vec, list) or not vec:
                    continue
                try:
                    v = np.asarray(vec, dtype=np.float32)
                except Exception:
                    continue
                if v.ndim != 1:
                    continue
                if self.dim is None:
                    self.dim = len(v)
                    self.wiersze = _Bufor(np.float32, self.dim)
                if len(v) != self.dim:
                    continue
                skrot = hashlib.md5(v.tobytes()).digest()
                w = self.skrot_wiersz.get(skrot)
                if w is None:
                    norma = float(np.linalg.norm(v))
                    w = self.wiersze.dopisz(v / norma if norma > 0 else v)
                    self.skrot_wiersz[skrot] = w
                self.wiersz_rek.dopisz(w)
                self.offset_rek.dopisz(start)
                self.id_rek.append(obj.get('id'))
                nowe += 1
            self.przeczytane = poz
        return nowe

    def odswiez(self, sciezka: Path, przepusc: Callable[[Dict[str, Any]], bool]) -> Dict[str, Any]:
        try:
            wynik = self._odswiez(sciezka, przepusc)
        except BaseException:
            self._gotowa.clear()
            self._wyczysc(None)
            raise
        self._gotowa.set()
        return wynik

    def _odswiez(self, sciezka: Path, przepusc: Callable[[Dict[str, Any]], bool]) -> Dict[str, Any]:
        st = sciezka.stat()
        pelna = self.sciezka != sciezka or self.inode != st.st_ino or st.st_size < self.przeczytane
        if pelna:
            self._gotowa.clear()
            self._wyczysc(sciezka)
            self.inode = st.st_ino
            print(f'[pamięć] buduję macierz z {st.st_size / 2 ** 30:.2f} GB indeksu — inicjalizacja przy pierwszym pytaniu…', flush=True)
            t0 = time.time()
        nowe = self._czytaj_od(sciezka, self.przeczytane, przepusc) if st.st_size > self.przeczytane else 0
        if pelna:
            print(f'[pamięć] macierz gotowa w {time.time() - t0:.1f} s: {len(self.id_rek)} rekordów, {(self.wiersze.n if self.wiersze else 0)} różnych wektorów', flush=True)
        return {'pelna': pelna, 'nowe': nowe, 'rekordy': len(self.id_rek), 'wiersze': self.wiersze.n if self.wiersze else 0}

    def najlepsze(self, sciezka: Path, qvec: List[float], ile: int, przepusc: Callable[[Dict[str, Any]], bool], nieswieze: Set[str], przetworz: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Optional[List[Tuple[float, Dict[str, Any]]]]:
        with self._lock:
            self.odswiez(sciezka, przepusc)
            if self.wiersze is None or self.dim != len(qvec) or (not self.id_rek):
                return None
            q = np.asarray(qvec, dtype=np.float32)
            qn = float(np.linalg.norm(q))
            if qn <= 0:
                return None
            przybl = (self.wiersze.widok() @ (q / qn))[self.wiersz_rek.widok()]
            if nieswieze:
                maska = np.fromiter((i in nieswieze for i in self.id_rek), dtype=bool, count=len(self.id_rek))
                przybl = np.where(maska, -np.inf, przybl)
            skonczone = np.isfinite(przybl)
            k = min(ile, int(skonczone.sum()))
            if k <= 0:
                return []
            prog = -np.partition(-przybl, k - 1)[k - 1] - 2 * BLAD_F32
            kand = np.nonzero(skonczone & (przybl >= prog))[0]
            offsety = self.offset_rek.widok()[kand]
            qv = np.array(qvec, dtype=float)
            qnorm = float(np.linalg.norm(qv)) + 1e-12
            wyniki: List[Tuple[float, int, Dict[str, Any]]] = []
            with sciezka.open('rb') as f:
                for nr, off in zip(kand.tolist(), offsety.tolist()):
                    f.seek(off)
                    obj = json.loads(f.readline().decode('utf-8', errors='ignore').strip())
                    v = np.array(obj['vector'], dtype=float)
                    score = float(np.dot(qv, v) / (qnorm * (float(np.linalg.norm(v)) + 1e-12)))
                    wyniki.append((score, nr, przetworz(obj)))
            wyniki.sort(key=lambda t: (-t[0], t[1]))
            return [(s, r) for s, _, r in wyniki[:ile]]
_MACIERZ = MacierzIndeksu()

def macierz() -> MacierzIndeksu:
    return _MACIERZ
