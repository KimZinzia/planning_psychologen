"""Opslag van de invoer en het laatste resultaat van de app in een SQLite-database.

Elke invoertabel uit data.TABELLEN wordt als losse databasetabel bewaard,
met dezelfde kolommen. Het laatste planningsresultaat staat in de tabel
'Resultaat' en de bijbehorende kerncijfers in 'Meta'.

Het pad van de database is in te stellen met de omgevingsvariabele
PLANNING_DB (standaard data/planning.db). De map data/ staat in .gitignore:
de database bevat namen van medewerkers en hoort niet in git.
"""

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pandas as pd

from data import TABELLEN, normaliseer_tabellen

STANDAARD_PAD = Path(__file__).resolve().parent.parent / "data" / "planning.db"


def db_pad() -> Path:
    return Path(os.environ.get("PLANNING_DB", STANDAARD_PAD))


def _verbind() -> sqlite3.Connection:
    pad = db_pad()
    pad.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(pad)


def _tabel_bestaat(con, naam: str) -> bool:
    rij = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (naam,)).fetchone()
    return rij is not None


def heeft_gegevens() -> bool:
    with closing(_verbind()) as con:
        return _tabel_bestaat(con, "Employees")


def lees_tabellen() -> dict[str, pd.DataFrame]:
    """Alle invoertabellen (ontbrekende tabellen komen terug als lege tabel)."""
    with closing(_verbind()) as con:
        tabellen = {
            naam: pd.read_sql(f'SELECT * FROM "{naam}"', con) if _tabel_bestaat(con, naam) else None
            for naam in TABELLEN
        }
    return normaliseer_tabellen(tabellen)


def schrijf_tabel(naam: str, df: pd.DataFrame) -> None:
    assert naam in TABELLEN, naam
    df = normaliseer_tabellen({naam: df})[naam]
    with closing(_verbind()) as con, con:
        df.to_sql(naam, con, if_exists="replace", index=False)


def schrijf_tabellen(tabellen: dict[str, pd.DataFrame]) -> None:
    """Vervangt álle invoertabellen in één transactie (bv. na een Excel-import)."""
    tabellen = normaliseer_tabellen(tabellen)
    with closing(_verbind()) as con, con:
        for naam, df in tabellen.items():
            df.to_sql(naam, con, if_exists="replace", index=False)


def bewaar_resultaat(rooster: pd.DataFrame, kpi: dict) -> None:
    meta = {"kpi": kpi, "tijdstip": datetime.now().isoformat(timespec="seconds")}
    with closing(_verbind()) as con, con:
        rooster.to_sql("Resultaat", con, if_exists="replace", index=False)
        con.execute("CREATE TABLE IF NOT EXISTS Meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT OR REPLACE INTO Meta VALUES ('resultaat', ?)", (json.dumps(meta),))


def lees_resultaat() -> tuple[pd.DataFrame, dict] | None:
    """Het laatst bewaarde resultaat als (rooster, meta), of None."""
    with closing(_verbind()) as con:
        if not (_tabel_bestaat(con, "Resultaat") and _tabel_bestaat(con, "Meta")):
            return None
        rij = con.execute("SELECT value FROM Meta WHERE key='resultaat'").fetchone()
        if rij is None:
            return None
        return pd.read_sql("SELECT * FROM Resultaat", con), json.loads(rij[0])


def wis_resultaat() -> None:
    """Het bewaarde resultaat hoort niet meer bij de invoer (bv. na een import)."""
    with closing(_verbind()) as con, con:
        con.execute("DROP TABLE IF EXISTS Resultaat")
        if _tabel_bestaat(con, "Meta"):
            con.execute("DELETE FROM Meta WHERE key='resultaat'")


# ---------------------------------------------------------------- archief
# Bewaarde planningen (bijv. per periode), elk met een naam, tijdstip,
# kerncijfers en het rooster zelf.

def _maak_archief(con) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS Archief ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, naam TEXT NOT NULL, tijdstip TEXT NOT NULL, kpi TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS ArchiefRooster ("
        "archief_id INTEGER NOT NULL, medewerker TEXT, afdeling TEXT, locatie TEXT, uren REAL)"
    )


def archiveer(naam: str, rooster: pd.DataFrame, kpi: dict) -> int:
    """Bewaart een planning in het archief en geeft het id terug."""
    with closing(_verbind()) as con, con:
        _maak_archief(con)
        cur = con.execute(
            "INSERT INTO Archief (naam, tijdstip, kpi) VALUES (?, ?, ?)",
            (naam, datetime.now().isoformat(timespec="seconds"), json.dumps(kpi)),
        )
        archief_id = cur.lastrowid
        r = rooster[["medewerker", "afdeling", "locatie", "uren"]].assign(archief_id=archief_id)
        r.to_sql("ArchiefRooster", con, if_exists="append", index=False)
    return archief_id


def archief_lijst() -> pd.DataFrame:
    """Overzicht van het archief: id, naam, tijdstip, kpi (dict); nieuwste eerst."""
    with closing(_verbind()) as con:
        _maak_archief(con)
        df = pd.read_sql("SELECT id, naam, tijdstip, kpi FROM Archief ORDER BY tijdstip DESC, id DESC", con)
    df["kpi"] = df["kpi"].map(json.loads)
    return df


def archief_rooster(archief_id: int) -> pd.DataFrame:
    with closing(_verbind()) as con:
        _maak_archief(con)
        return pd.read_sql(
            "SELECT medewerker, afdeling, locatie, uren FROM ArchiefRooster WHERE archief_id = ?",
            con,
            params=(archief_id,),
        )


def archief_verwijder(archief_id: int) -> None:
    with closing(_verbind()) as con, con:
        _maak_archief(con)
        con.execute("DELETE FROM ArchiefRooster WHERE archief_id = ?", (archief_id,))
        con.execute("DELETE FROM Archief WHERE id = ?", (archief_id,))
