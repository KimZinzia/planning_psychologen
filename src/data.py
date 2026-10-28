"""Zet de invoertabellen om naar de parameters uit de wiskundige formulering
(sets E, D, L en de data req, avail, cur, gz, gz_only, l1/l2, ...).

De invoer bestaat uit een handvol tabellen (pandas DataFrames), met dezelfde
namen en kolommen als de tabbladen van planning_input.xlsx:

- Employees:       medewerker, gz_psycholoog, In opleiding, Contracturen,
                   afwezigheids-/takenkolommen (zie AFTREK_KOLOMMEN),
                   zwangerschapsverlof en werkdagen (ma..vr, informatief).
                   'Inzetbare tijd' wordt hieruit berekend.
- Locaties:        lijst van locaties (alleen app; keuzelijst bij invoer).
- Requirements:    locatie, afdeling, vereiste_uren, alleen_gz_psycholoog,
                   max_medewerkers, afdelingsgroep.
- CurrentSchedule: medewerker, afdeling, locatie, huidige_uren.
- MinimumUren:     medewerker, afdeling óf afdelingsgroep, locatie, uren.
- Config:          key/value met locatie_1 en locatie_2 (het reispaar).
- Bijsturing:      medewerker, afdeling, locatie, actie, uren (alleen app).

Die tabellen komen óf uit het Excel-bestand (load_excel_tables, gebruikt door
het notebook), óf uit de database van de app (store.py). params_from_tables
maakt er in beide gevallen hetzelfde Params-object van; model.py kent alleen
dat Params-object.

Er is geen aparte kwalificatie-laag: de enige reden dat een medewerker een
afdeling niet mag doen is wanneer die afdeling 'alleen_gz_psycholoog' is en
de medewerker geen gz-psycholoog is (zie gz_only / constraint 7 in model.py).

Periode-specifieke vereisten (bv. "medewerker X moet minimaal Y uur GRZ op ONO
doen", "max 1 medewerker op Lotus 2") staan niet hardcoded in model.py:
- Requirements.max_medewerkers: headcount-cap per afdeling.
- Requirements.afdelingsgroep: label (bv. "GRZ") waarmee afdelingen aan
  elkaar gekoppeld worden; een medewerker die in zo'n groep werkt, moet dat
  op één locatie doen (zie constraint 9 in model.py).
- MinimumUren: verplicht minimumaantal uren per medewerker, voor een
  afdeling óf een afdelingsgroep (precies één van de twee invullen), optioneel
  toegespitst op een locatie. Uren leeg laten betekent: moet er zijn, aantal
  maakt niet uit.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# Kolommen in Employees die van de contracturen worden afgetrokken. De
# inzetbare tijd is (zie bereken_inzetbare_tijd):
#   0                                            bij zwangerschapsverlof
#   MAX(0, Contracturen - som(aftrekkolommen))   anders,
#   en daarvan PIOG_PRODUCTIVITEIT als iemand in opleiding is.
# (De oude Excel-formule had die laatste stap nog niet.)
AFTREK_KOLOMMEN = [
    "lesuren PIOG",
    "Opleidingsuren PIOG",
    "extern opleiding",
    "ouderschapsverlof",
    "overig",
    "Opleidingstijd",
    "Coordinerende taken",
]

# Productiviteit van een medewerker in opleiding (PIOG): telt voor 75%.
PIOG_PRODUCTIVITEIT = 0.75

# Werkdagen van een medewerker. Het model plant per week, maar gebruikt de
# werkdagen voor de MDO's: wie op een afdeling werkt, moet werken op de dag
# van het MDO van die afdeling (Requirements.mdo_dag, constraint 12).
WERKDAGEN = ["ma", "di", "wo", "do", "vr"]
DAG_NAMEN = {"ma": "maandag", "di": "dinsdag", "wo": "woensdag", "do": "donderdag", "vr": "vrijdag"}

TABELLEN = ["Employees", "Locaties", "Requirements", "CurrentSchedule", "MinimumUren", "Config", "Bijsturing"]

KOLOMMEN = {
    "Employees": ["medewerker", "gz_psycholoog", "In opleiding", "Contracturen", *AFTREK_KOLOMMEN, "zwangerschapsverlof", *WERKDAGEN],
    "Locaties": ["locatie"],
    "Requirements": ["locatie", "afdeling", "vereiste_uren", "alleen_gz_psycholoog", "max_medewerkers", "afdelingsgroep", "mdo_dag"],
    "CurrentSchedule": ["medewerker", "afdeling", "locatie", "huidige_uren"],
    "MinimumUren": ["medewerker", "afdeling", "afdelingsgroep", "locatie", "uren"],
    "Config": ["key", "value"],
    "Bijsturing": ["medewerker", "afdeling", "locatie", "actie", "uren"],
}

BIJSTURING_ACTIES = ["vastzetten", "uitsluiten"]

# Kolommen die tekst bevatten (namen, labels, dagen); de rest is numeriek.
TEKST_KOLOMMEN = {"medewerker", "afdeling", "locatie", "afdelingsgroep", "mdo_dag", "actie", "key", "value"}


@dataclass
class Params:
    E: list[str]  # medewerkers
    D: list[str]  # afdelingen
    L: list[str]  # locaties
    req: dict[tuple[str, str], float]       # req[d, l]
    avail: dict[str, float]                 # avail[e]
    cur: dict[tuple[str, str, str], float]  # cur[e, d, l], default 0
    gz: dict[str, int]                      # gz[e]
    gz_only: dict[tuple[str, str], int]     # gz_only[d, l], default 0
    max_medewerkers: dict[str, int]         # max_medewerkers[d], default geen cap
    afdelingsgroep: dict[str, str]          # afdelingsgroep[d], default geen groep
    # (medewerker, afdeling of None, afdelingsgroep of None, locatie of None, uren of None)
    # precies één van afdeling/afdelingsgroep is gezet; uren=None => moet er zijn, aantal maakt niet uit
    min_uren: list[tuple[str, str | None, str | None, str | None, float | None]]
    l1: str  # eerste locatie van het te vermijden reispaar
    l2: str  # tweede locatie van het te vermijden reispaar
    # (medewerker, afdeling, locatie of None, 'vastzetten'/'uitsluiten', uren of None)
    bijsturing: list[tuple[str, str, str | None, str, float | None]] = field(default_factory=list)
    # werkdagen[e]: dagen waarop e werkt; leeg = onbekend (geen beperking)
    werkdagen: dict[str, set[str]] = field(default_factory=dict)
    # mdo_dag[d, l]: dag van het MDO van afdeling d op locatie l; ontbreekt = geen MDO
    mdo_dag: dict[tuple[str, str], str] = field(default_factory=dict)


def bereken_uren_opbouw(employees: pd.DataFrame) -> pd.DataFrame:
    """Opbouw van de inzetbare tijd per medewerker, stap voor stap:

    - netto:          contracturen min de aftrekposten (nooit minder dan 0);
    - piog_correctie: bij iemand in opleiding (PIOG) telt de productiviteit
                      voor PIOG_PRODUCTIVITEIT, dus gaat er 25% van de netto
                      uren af (negatief getal; 0 voor anderen);
    - inzetbaar:      netto + piog_correctie, of 0 bij zwangerschapsverlof.

    Voorbeeld: 28 contracturen - 4 lesuren - 6 opleidingsuren = 18 netto;
    PIOG: -4,5; inzetbaar 13,5 uur.
    """
    aftrek = sum(pd.to_numeric(employees[k], errors="coerce").fillna(0) for k in AFTREK_KOLOMMEN)
    contract = pd.to_numeric(employees["Contracturen"], errors="coerce").fillna(0)
    netto = (contract - aftrek).clip(lower=0)
    in_opleiding = pd.to_numeric(employees["In opleiding"], errors="coerce").fillna(0) == 1
    piog_correctie = (-(1 - PIOG_PRODUCTIVITEIT) * netto).where(in_opleiding, 0.0)
    zwanger = pd.to_numeric(employees["zwangerschapsverlof"], errors="coerce").fillna(0) == 1
    inzetbaar = (netto + piog_correctie).where(~zwanger, 0.0)
    return pd.DataFrame({"netto": netto, "piog_correctie": piog_correctie, "inzetbaar": inzetbaar})


def bereken_inzetbare_tijd(employees: pd.DataFrame) -> pd.Series:
    """Uren per week dat een medewerker op afdelingen ingepland kan worden
    (zie bereken_uren_opbouw voor de opbouw)."""
    return bereken_uren_opbouw(employees)["inzetbaar"]


def _leeg_naar_none(waarde):
    if waarde is None or (isinstance(waarde, float) and pd.isna(waarde)):
        return None
    if isinstance(waarde, str) and waarde.strip() == "":
        return None
    return waarde


def normaliseer_tabellen(tabellen: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Zorgt dat elke tabel bestaat en alle verwachte kolommen heeft (lege
    optionele kolommen worden aangevuld), en verwijdert lege rijen."""
    result = {}
    for naam in TABELLEN:
        df = tabellen.get(naam)
        df = pd.DataFrame(columns=KOLOMMEN[naam]) if df is None else df.copy()
        for kol in KOLOMMEN[naam]:
            if kol not in df.columns:
                df[kol] = None
        df = df[KOLOMMEN[naam]]
        # Tekstkolommen altijd als tekst (ook als ze helemaal leeg zijn, anders
        # ziet pandas ze als getallen) en spaties voor/achter weghalen:
        # 'ONO ' en 'ONO' zijn dezelfde locatie.
        for kol in df.columns:
            if kol in TEKST_KOLOMMEN:
                df[kol] = df[kol].map(lambda w: _leeg_naar_none(w.strip() if isinstance(w, str) else w)).astype(object)
            elif not pd.api.types.is_numeric_dtype(df[kol]):
                df[kol] = df[kol].map(lambda w: w.strip() if isinstance(w, str) else w)
        sleutel = KOLOMMEN[naam][0]
        df = df[df[sleutel].map(_leeg_naar_none).notna()]
        result[naam] = df.reset_index(drop=True)
    return result


def load_excel_tables(path) -> dict[str, pd.DataFrame]:
    """Leest alle tabbladen uit het Excel-bestand (alleen lezen, het bestand
    wordt nooit aangepast). `path` mag een pad zijn of een geopend bestand
    (bv. een upload in de app)."""
    sheets = pd.read_excel(path, sheet_name=None)
    employees = sheets.get("Employees")
    if employees is not None and "Contracturen" not in employees and "Inzetbare tijd" in employees:
        # Ouder formaat (bv. toy_example.xlsx): alleen 'Inzetbare tijd', geen
        # opbouw uit contracturen. Dan zijn de contracturen gelijk daaraan.
        sheets["Employees"] = employees.assign(Contracturen=employees["Inzetbare tijd"])
    return normaliseer_tabellen({naam: sheets.get(naam) for naam in TABELLEN})


def params_from_tables(tabellen: dict[str, pd.DataFrame]) -> Params:
    t = normaliseer_tabellen(tabellen)
    employees, requirements = t["Employees"], t["Requirements"]
    config = t["Config"].set_index("key")["value"]

    E = [str(e) for e in employees["medewerker"]]
    D = sorted(requirements["afdeling"].unique())
    L = sorted(requirements["locatie"].unique())

    avail = dict(zip(E, bereken_inzetbare_tijd(employees).astype(float)))
    gz = dict(zip(E, pd.to_numeric(employees["gz_psycholoog"], errors="coerce").fillna(0).astype(int)))

    req = {(row.afdeling, row.locatie): float(row.vereiste_uren) for row in requirements.itertuples()}

    gz_only = {
        (row.afdeling, row.locatie): int(row.alleen_gz_psycholoog)
        for row in requirements.itertuples()
        if _leeg_naar_none(row.alleen_gz_psycholoog) is not None
    }
    # headcount-cap per afdeling (bv. max 1 medewerker op Lotus 2)
    max_medewerkers = {
        row.afdeling: int(row.max_medewerkers)
        for row in requirements.itertuples()
        if _leeg_naar_none(row.max_medewerkers) is not None
    }
    # groep-label (bv. "GRZ") dat afdelingen aan elkaar koppelt
    afdelingsgroep = {
        row.afdeling: row.afdelingsgroep
        for row in requirements.itertuples()
        if _leeg_naar_none(row.afdelingsgroep) is not None
    }

    cur = {
        (row.medewerker, row.afdeling, row.locatie): float(row.huidige_uren)
        for row in t["CurrentSchedule"].itertuples()
        if _leeg_naar_none(row.huidige_uren) is not None
    }

    min_uren = [
        (
            row.medewerker,
            _leeg_naar_none(row.afdeling),
            _leeg_naar_none(row.afdelingsgroep),
            _leeg_naar_none(row.locatie),
            None if _leeg_naar_none(row.uren) is None else float(row.uren),
        )
        for row in t["MinimumUren"].itertuples()
    ]

    bijsturing = [
        (
            row.medewerker,
            row.afdeling,
            _leeg_naar_none(row.locatie),
            row.actie,
            None if _leeg_naar_none(row.uren) is None else float(row.uren),
        )
        for row in t["Bijsturing"].itertuples()
    ]

    return Params(
        E=E,
        D=D,
        L=L,
        req=req,
        avail=avail,
        cur=cur,
        gz=gz,
        gz_only=gz_only,
        max_medewerkers=max_medewerkers,
        afdelingsgroep=afdelingsgroep,
        min_uren=min_uren,
        l1=config.get("locatie_1"),
        l2=config.get("locatie_2"),
        bijsturing=bijsturing,
        werkdagen={
            str(row.medewerker): {d for d in WERKDAGEN if pd.to_numeric(getattr(row, d), errors="coerce") == 1}
            for row in employees.itertuples()
        },
        mdo_dag={
            (row.afdeling, row.locatie): str(row.mdo_dag).strip().lower()[:2]
            for row in requirements.itertuples()
            if _leeg_naar_none(row.mdo_dag) is not None
        },
    )


def load_data(path: str | Path) -> Params:
    """Leest planning_input.xlsx en geeft het Params-object terug (voor het notebook)."""
    return params_from_tables(load_excel_tables(path))


def valideer(p: Params) -> list[str]:
    """Controleert de invoer op tikfouten en verwijzingen die niet bestaan.
    Geeft een lijst foutmeldingen terug (leeg = alles in orde)."""
    fouten = []
    E, D, L = set(p.E), set(p.D), set(p.L)
    groepen = set(p.afdelingsgroep.values())

    if len(p.E) != len(E):
        dubbel = sorted({e for e in p.E if p.E.count(e) > 1})
        fouten.append(f"Medewerkers staan dubbel in de lijst: {', '.join(dubbel)}")
    for naam, loc in [("locatie_1", p.l1), ("locatie_2", p.l2)]:
        if loc not in L:
            fouten.append(f"Instelling {naam} = '{loc}' is geen bekende locatie ({', '.join(sorted(L))}).")
    for (e, d, l) in p.cur:
        if e not in E:
            fouten.append(f"Huidige planning: medewerker '{e}' bestaat niet.")
        elif (d, l) not in p.req:
            fouten.append(f"Huidige planning: afdeling '{d}' op locatie '{l}' staat niet in de vereisten.")
    for (e, d, groep, l, _u) in p.min_uren:
        if e not in E:
            fouten.append(f"Minimumuren: medewerker '{e}' bestaat niet.")
        if (d is None) == (groep is None):
            fouten.append(f"Minimumuren ({e}): vul precies één van afdeling of afdelingsgroep in.")
        if d is not None and d not in D:
            fouten.append(f"Minimumuren ({e}): afdeling '{d}' bestaat niet.")
        if groep is not None and groep not in groepen:
            fouten.append(f"Minimumuren ({e}): afdelingsgroep '{groep}' bestaat niet.")
        if l is not None and l not in L:
            fouten.append(f"Minimumuren ({e}): locatie '{l}' bestaat niet.")
    for (d, l), dag in p.mdo_dag.items():
        if dag not in WERKDAGEN:
            fouten.append(f"Afdeling {d} ({l}): MDO-dag '{dag}' is geen werkdag (ma, di, wo, do of vr).")
    for (e, d, l, actie, _u) in p.bijsturing:
        if e not in E:
            fouten.append(f"Bijsturing: medewerker '{e}' bestaat niet.")
        if d not in D:
            fouten.append(f"Bijsturing ({e}): afdeling '{d}' bestaat niet.")
        if l is not None and (d, l) not in p.req:
            fouten.append(f"Bijsturing ({e}): afdeling '{d}' bestaat niet op locatie '{l}'.")
        if actie not in BIJSTURING_ACTIES:
            fouten.append(f"Bijsturing ({e}): actie moet 'vastzetten' of 'uitsluiten' zijn.")
    return fouten
