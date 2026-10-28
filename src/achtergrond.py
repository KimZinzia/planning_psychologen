"""Planning berekenen op de achtergrond.

Streamlit stopt alles wat een pagina aan het doen is zodra de gebruiker naar
een andere pagina gaat. Daarom draait het plannen hier in een eigen thread:
die loopt door, ook als je wegklikt, en schrijft het resultaat zelf naar de
database. De pagina's vragen alleen de voortgang op (status()).

Er draait hooguit één berekening tegelijk. Zolang die loopt, horen er geen
wijzigingen in de invoer te worden opgeslagen (zie bezig()); de berekening
gebruikt een momentopname van de invoer van het moment van starten.

HiGHS geeft tijdens het rekenen de Python-lock (GIL) vrij, dus de app blijft
gewoon bruikbaar terwijl er gerekend wordt.
"""

import threading
import time
import traceback
from dataclasses import dataclass, field, replace

import pandas as pd

import store
from data import params_from_tables
from solve import STAPPEN, TIJDSLIMIET_PER_STAP, OnoplosbaarError, plan


@dataclass
class Taak:
    gestart: float
    stap: int = 0  # 1..len(STAPPEN)
    omschrijving: str = ""
    stap_gestart: float = 0.0
    klaar: bool = False
    geslaagd: bool = False
    fout: str | None = None  # technische fout
    onoplosbaar: list[str] | None = None  # knelpunten (sleutels uit CONSTRAINT_GROEPEN)
    beeindigd: float | None = None
    stap_tijden: list[float] = field(default_factory=list)

    @property
    def loopt(self) -> bool:
        return not self.klaar

    def verstreken(self) -> float:
        return (self.beeindigd or time.time()) - self.gestart

    def fractie(self) -> float:
        """Schatting van de voortgang (0..1). Elke stap telt even zwaar; binnen
        een stap schatten we op basis van de tijdslimiet per stap."""
        if self.klaar:
            return 1.0
        if self.stap == 0:
            return 0.0
        binnen = min((time.time() - self.stap_gestart) / TIJDSLIMIET_PER_STAP, 0.95)
        return (self.stap - 1 + binnen) / len(STAPPEN)


_lock = threading.Lock()
_taak: Taak | None = None


def status() -> Taak | None:
    """Kopie van de huidige (of laatst afgeronde) taak, of None."""
    with _lock:
        return replace(_taak) if _taak else None


def bezig() -> bool:
    t = status()
    return t is not None and t.loopt


def wis_afgerond() -> None:
    """Vergeet een afgeronde taak (bijv. nadat de melding is getoond)."""
    global _taak
    with _lock:
        if _taak and _taak.klaar:
            _taak = None


def start(tabellen: dict[str, pd.DataFrame], invoer_hash: str) -> bool:
    """Start een berekening op de achtergrond. False als er al een loopt."""
    global _taak
    with _lock:
        if _taak and _taak.loopt:
            return False
        _taak = Taak(gestart=time.time())
    threading.Thread(target=_werk, args=(tabellen, invoer_hash), daemon=True, name="planning").start()
    return True


def _bij(**velden) -> None:
    with _lock:
        for k, v in velden.items():
            setattr(_taak, k, v)


def _voortgang(stap: int, omschrijving: str) -> None:
    with _lock:
        nu = time.time()
        if _taak.stap and stap != _taak.stap:
            _taak.stap_tijden.append(nu - _taak.stap_gestart)
        _taak.stap, _taak.omschrijving, _taak.stap_gestart = stap, omschrijving, nu


def _werk(tabellen: dict[str, pd.DataFrame], invoer_hash: str) -> None:
    try:
        p = params_from_tables(tabellen)
        resultaat = plan(p, voortgang=_voortgang)
        store.bewaar_resultaat(resultaat.rooster, dict(resultaat.kpi, invoer=invoer_hash))
        _bij(geslaagd=True)
    except OnoplosbaarError as fout:
        _bij(onoplosbaar=fout.oorzaken)
    except Exception as fout:  # noqa: BLE001 - elke fout moet in de app zichtbaar worden
        traceback.print_exc()
        _bij(fout=str(fout) or type(fout).__name__)
    finally:
        _bij(klaar=True, beeindigd=time.time())
