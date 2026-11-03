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
- Afspraken:       medewerker, afdeling óf afdelingsgroep (of geen van beide =
                   hele locatie), locatie, regel (minimaal/precies/uitsluiten),
                   uren. Vervangt de oude tabellen MinimumUren en Bijsturing,
                   die bij het lezen automatisch worden omgezet.
- Config:          key/value met locatie_1 en locatie_2 (het reispaar: liever
                   niet combineren) en nooit_1 en nooit_2 (nooit combineren).

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
- Afspraken: per medewerker 'minimaal X uur', 'precies X uur' of
  'uitsluiten', voor een afdeling, een afdelingsgroep of een hele locatie.
  Uren leeg bij 'minimaal' betekent: moet er werken, aantal maakt niet uit.
"""

import re
from dataclasses import dataclass, field, replace
from datetime import date
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

# Basispsychologen (geen gz, niet in opleiding) liefst niet voller dan dit deel
# van hun inzetbare uren. Standaardwaarde; in te stellen in de app (Config).
MAX_BEZETTING_BASIS = 0.85
# Streefwaarden bij het verdelen van de uren (sterke voorkeur, geen harde grens):
# gz-psychologen zo dicht mogelijk bij STREEF_BEZETTING_GZ, de anderen liefst
# niet onder MIN_BEZETTING_NIET_GZ. In te stellen in de app (Config).
STREEF_BEZETTING_GZ = 0.90
MIN_BEZETTING_NIET_GZ = 0.75
MAX_BEZETTING_NIET_GZ = 0.85

# Werkdagen van een medewerker. Het model plant per week, maar gebruikt de
# werkdagen voor de MDO's: wie op een afdeling werkt, moet werken op de dag
# van het MDO van die afdeling (Requirements.mdo_dag, constraint 11). Een
# afdeling kan meerdere MDO-dagen hebben (bv. 'di,do'), zie model.py.
WERKDAGEN = ["ma", "di", "wo", "do", "vr"]
DAG_NAMEN = {"ma": "maandag", "di": "dinsdag", "wo": "woensdag", "do": "donderdag", "vr": "vrijdag"}

TABELLEN = [
    "Employees", "Locaties", "Requirements", "CurrentSchedule", "Afspraken", "Config",
    # Noodplanning: tijdelijke aanpassingen met een einddatum (zie pas_tijdelijk_toe)
    "TijdelijkAfdeling", "TijdelijkMedewerker", "TijdelijkRegels",
]
# Oude tabellen (vóór november 2026); worden bij het lezen omgezet naar Afspraken.
OUDE_TABELLEN = ["MinimumUren", "Bijsturing"]

KOLOMMEN = {
    "Employees": ["medewerker", "gz_psycholoog", "In opleiding", "Contracturen", *AFTREK_KOLOMMEN, "zwangerschapsverlof", *WERKDAGEN],
    "Locaties": ["locatie"],
    "Requirements": ["locatie", "afdeling", "vereiste_uren", "alleen_gz_psycholoog", "max_medewerkers", "afdelingsgroep", "mdo_dag", "voorrang"],
    "CurrentSchedule": ["medewerker", "afdeling", "locatie", "huidige_uren"],
    "Afspraken": ["medewerker", "afdeling", "afdelingsgroep", "locatie", "regel", "uren"],
    "Config": ["key", "value"],
    "TijdelijkAfdeling": ["afdeling", "locatie", "uren", "tot", "notitie"],
    "TijdelijkMedewerker": ["medewerker", "uren_erbij", "tot", "notitie"],
    "TijdelijkRegels": ["regel", "tot", "notitie"],
}

# Regels die in de noodplanning tijdelijk losgelaten mogen worden.
LOSLAATBARE_REGELS = {
    "nooit_combineren": "Nooit combineren (locaties)",
    "max_medewerkers": "Max. medewerkers per afdeling",
}

# Regels in de tabel Afspraken:
# - minimaal:   e werkt daar minstens 'uren' uur (leeg = moet er werken, aantal maakt niet uit);
# - precies:    e werkt daar precies 'uren' uur;
# - uitsluiten: e werkt daar niet.
REGELS = ["minimaal", "precies", "uitsluiten"]

# Kolommen die tekst bevatten (namen, labels, dagen); de rest is numeriek.
TEKST_KOLOMMEN = {"medewerker", "afdeling", "locatie", "afdelingsgroep", "mdo_dag", "regel", "key", "value", "tot", "notitie"}


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
    l1: str  # eerste locatie van het te vermijden reispaar
    l2: str  # tweede locatie van het te vermijden reispaar
    # (medewerker, afdeling of None, afdelingsgroep of None, locatie of None, regel, uren of None)
    # afdeling en groep allebei None => hele locatie. Regel: zie REGELS.
    afspraken: list[tuple[str, str | None, str | None, str | None, str, float | None]] = field(default_factory=list)
    # werkdagen[e]: dagen waarop e werkt; leeg = onbekend (geen beperking)
    werkdagen: dict[str, set[str]] = field(default_factory=dict)
    # mdo_dag[d, l]: dag(en) van het MDO van afdeling d op locatie l, bv. ('di', 'do'); ontbreekt = geen MDO
    mdo_dag: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    # Locatieparen die één medewerker nooit mag combineren (harde eis), bv. ONO en RK
    nooit_samen: list[tuple[str, str]] = field(default_factory=list)
    # in_opleiding[e]: 1 = PIOG
    in_opleiding: dict[str, int] = field(default_factory=dict)
    # Basispsychologen (geen gz, niet in opleiding): liefst niet voller dan dit deel van hun inzetbare uren
    max_bezetting_basis: float = MAX_BEZETTING_BASIS
    streef_bezetting_gz: float = STREEF_BEZETTING_GZ
    min_bezetting_niet_gz: float = MIN_BEZETTING_NIET_GZ
    max_bezetting_niet_gz: float = MAX_BEZETTING_NIET_GZ
    # voorrang[d, l] = 1: bij een tekort aan uren zo min mogelijk korten
    voorrang: dict[tuple[str, str], int] = field(default_factory=dict)
    # Vereiste uren zonder tijdelijke aanpassingen (alleen voor weergave)
    req_normaal: dict[tuple[str, str], float] = field(default_factory=dict)
    # Actieve tijdelijke aanpassingen (noodplanning), als leesbare zinnen
    tijdelijk: list[str] = field(default_factory=list)


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


def oude_naar_afspraken(minimum: pd.DataFrame | None, bijsturing: pd.DataFrame | None) -> pd.DataFrame:
    """Zet de oude tabellen MinimumUren en Bijsturing om naar Afspraken.

    - minimumuren                -> minimaal (uren blijven gelijk);
    - vastzetten zonder uren     -> minimaal zonder uren (moet er werken);
    - vastzetten met uren        -> precies;
    - uitsluiten                 -> uitsluiten.
    Betekenis en uitkomst van de planning blijven daarbij precies hetzelfde.
    """
    rijen = []
    if minimum is not None:
        for r in minimum.to_dict("records"):
            rijen.append({"medewerker": r.get("medewerker"), "afdeling": r.get("afdeling"),
                          "afdelingsgroep": r.get("afdelingsgroep"), "locatie": r.get("locatie"),
                          "regel": "minimaal", "uren": r.get("uren")})
    if bijsturing is not None:
        for r in bijsturing.to_dict("records"):
            uren = _leeg_naar_none(r.get("uren"))
            if r.get("actie") == "uitsluiten":
                regel, uren = "uitsluiten", None
            else:
                regel = "minimaal" if uren is None else "precies"
            rijen.append({"medewerker": r.get("medewerker"), "afdeling": r.get("afdeling"), "afdelingsgroep": None,
                          "locatie": r.get("locatie"), "regel": regel, "uren": uren})
    return pd.DataFrame(rijen, columns=KOLOMMEN["Afspraken"])


def normaliseer_tabellen(tabellen: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Zorgt dat elke tabel bestaat en alle verwachte kolommen heeft (lege
    optionele kolommen worden aangevuld), en verwijdert lege rijen."""
    if tabellen.get("Afspraken") is None and any(tabellen.get(n) is not None for n in OUDE_TABELLEN):
        tabellen = {**tabellen, "Afspraken": oude_naar_afspraken(tabellen.get("MinimumUren"), tabellen.get("Bijsturing"))}
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
    return normaliseer_tabellen({naam: sheets.get(naam) for naam in [*TABELLEN, *OUDE_TABELLEN]})


def params_from_tables(tabellen: dict[str, pd.DataFrame], peildatum: date | None = None) -> Params:
    """Tabellen -> Params, inclusief de tijdelijke aanpassingen (noodplanning)
    die op `peildatum` (standaard vandaag) nog gelden."""
    p = _params_zonder_tijdelijk(tabellen)
    return pas_tijdelijk_toe(p, normaliseer_tabellen(tabellen), peildatum or date.today())


def _params_zonder_tijdelijk(tabellen: dict[str, pd.DataFrame]) -> Params:
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

    afspraken = [
        (
            row.medewerker,
            _leeg_naar_none(row.afdeling),
            _leeg_naar_none(row.afdelingsgroep),
            _leeg_naar_none(row.locatie),
            row.regel,
            None if _leeg_naar_none(row.uren) is None else float(row.uren),
        )
        for row in t["Afspraken"].itertuples()
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
        l1=config.get("locatie_1"),
        l2=config.get("locatie_2"),
        afspraken=afspraken,
        werkdagen={
            str(row.medewerker): {d for d in WERKDAGEN if pd.to_numeric(getattr(row, d), errors="coerce") == 1}
            for row in employees.itertuples()
        },
        mdo_dag={
            (row.afdeling, row.locatie): mdo_dagen(row.mdo_dag)
            for row in requirements.itertuples()
            if mdo_dagen(row.mdo_dag)
        },
        nooit_samen=_nooit_samen(config),
        in_opleiding=dict(zip(E, pd.to_numeric(employees["In opleiding"], errors="coerce").fillna(0).astype(int))),
        max_bezetting_basis=_percentage(config, "max_bezetting_basis", MAX_BEZETTING_BASIS),
        streef_bezetting_gz=_percentage(config, "streef_bezetting_gz", STREEF_BEZETTING_GZ),
        min_bezetting_niet_gz=_percentage(config, "min_bezetting_niet_gz", MIN_BEZETTING_NIET_GZ),
        max_bezetting_niet_gz=_percentage(config, "max_bezetting_niet_gz", MAX_BEZETTING_NIET_GZ),
        voorrang={
            (row.afdeling, row.locatie): 1
            for row in requirements.itertuples()
            if pd.to_numeric(row.voorrang, errors="coerce") == 1
        },
        req_normaal=dict(req),
    )


def mdo_dagen(waarde) -> tuple[str, ...]:
    """'di,do' / 'Dinsdag en donderdag' / ['di', 'do'] -> ('di', 'do'); leeg -> ().
    Onbekende woorden blijven staan (2 letters), zodat valideer() ze meldt."""
    if waarde is None or (not isinstance(waarde, (str, list, tuple)) and pd.isna(waarde)):
        return ()
    delen = waarde if isinstance(waarde, (list, tuple)) else re.split(r"[^a-z]+", str(waarde).lower())
    dagen = []
    for deel in delen:
        deel = str(deel).strip().lower()
        if deel and deel != "en" and deel[:2] not in dagen:
            dagen.append(deel[:2])
    volgorde = {d: i for i, d in enumerate(WERKDAGEN)}
    return tuple(sorted(dagen, key=lambda d: volgorde.get(d, 99)))


def mdo_tekst(dagen) -> str | None:
    """('di', 'do') -> 'di,do' (zo staat het in de database); leeg -> None."""
    dagen = mdo_dagen(dagen)
    return ",".join(dagen) if dagen else None


def lees_datum(waarde) -> date | None:
    """'2026-12-01' (zo staat het in de database) of een date -> date; anders None."""
    if isinstance(waarde, date):
        return waarde
    if _leeg_naar_none(waarde) is None:
        return None
    try:
        return date.fromisoformat(str(waarde)[:10])
    except ValueError:
        return None


def _nl_datum(d: date) -> str:
    return f"{d:%d-%m-%Y}"


def _getal(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def pas_tijdelijk_toe(p: Params, t: dict[str, pd.DataFrame], peildatum: date) -> Params:
    """Noodplanning: past de tijdelijke aanpassingen toe die op `peildatum` nog
    gelden (einddatum 'tot en met' >= peildatum). Verlopen of onvolledige regels
    worden genegeerd. De basisinvoer zelf blijft onaangeroerd.

    - TijdelijkAfdeling:   afdeling (op locatie) krijgt tijdelijk 'uren' vereist;
    - TijdelijkMedewerker: inzetbare uren + 'uren_erbij' (negatief = minder);
    - TijdelijkRegels:     regel uit LOSLAATBARE_REGELS geldt tijdelijk niet.
    """
    req, avail = dict(p.req), dict(p.avail)
    nooit_samen, max_medewerkers = list(p.nooit_samen), dict(p.max_medewerkers)
    zinnen = []

    def actief(rij) -> date | None:
        tot = lees_datum(rij["tot"])
        return tot if tot is not None and tot >= peildatum else None

    for rij in t["TijdelijkAfdeling"].to_dict("records"):
        tot, d, l = actief(rij), rij["afdeling"], rij["locatie"]
        uren = pd.to_numeric(rij["uren"], errors="coerce")
        if tot is None or pd.isna(uren) or (d, l) not in req:
            continue
        zinnen.append(f"{d} ({l}): {_getal(float(uren))} uur in plaats van {_getal(req[d, l])} (t/m {_nl_datum(tot)})")
        req[d, l] = max(float(uren), 0.0)
    for rij in t["TijdelijkMedewerker"].to_dict("records"):
        tot, e = actief(rij), rij["medewerker"]
        erbij = pd.to_numeric(rij["uren_erbij"], errors="coerce")
        if tot is None or pd.isna(erbij) or e not in avail:
            continue
        nieuw = max(avail[e] + float(erbij), 0.0)
        zinnen.append(f"{e}: {_getal(nieuw)} uur inzetbaar in plaats van {_getal(avail[e])} (t/m {_nl_datum(tot)})")
        avail[e] = nieuw
    for rij in t["TijdelijkRegels"].to_dict("records"):
        tot, regel = actief(rij), rij["regel"]
        if tot is None or regel not in LOSLAATBARE_REGELS:
            continue
        zinnen.append(f"Regel '{LOSLAATBARE_REGELS[regel]}' geldt tijdelijk niet (t/m {_nl_datum(tot)})")
        if regel == "nooit_combineren":
            nooit_samen = []
        elif regel == "max_medewerkers":
            max_medewerkers = {}
    return replace(p, req=req, avail=avail, nooit_samen=nooit_samen, max_medewerkers=max_medewerkers, tijdelijk=zinnen)


def _percentage(config, sleutel: str, standaard: float) -> float:
    """Config-percentage (bv. '85') -> 0.85; leeg of onleesbaar = standaard."""
    waarde = pd.to_numeric(config.get(sleutel), errors="coerce")
    return standaard if pd.isna(waarde) else float(waarde) / 100


def is_basispsycholoog(p: "Params", e: str) -> bool:
    """Geen gz-psycholoog en niet in opleiding."""
    return p.gz.get(e, 0) != 1 and p.in_opleiding.get(e, 0) != 1


def _nooit_samen(config) -> list[tuple[str, str]]:
    """Config nooit_1/nooit_2 -> [(a, b)]; leeg als niet (volledig) ingevuld."""
    a, b = _leeg_naar_none(config.get("nooit_1")), _leeg_naar_none(config.get("nooit_2"))
    return [(str(a), str(b))] if a is not None and b is not None else []


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
    for a, b in p.nooit_samen:
        if a == b:
            fouten.append("Instelling 'nooit combineren': kies twee verschillende locaties.")
        for loc in (a, b):
            if loc not in L:
                fouten.append(f"Instelling 'nooit combineren': '{loc}' is geen bekende locatie.")
        if {a, b} == {p.l1, p.l2}:
            fouten.append(f"{a} en {b} staan zowel bij 'nooit' als bij 'liever niet' combineren; kies er één.")
    for (e, d, l) in p.cur:
        if e not in E:
            fouten.append(f"Huidige planning: medewerker '{e}' bestaat niet.")
        elif (d, l) not in p.req:
            fouten.append(f"Huidige planning: afdeling '{d}' op locatie '{l}' staat niet in de vereisten.")
    for (d, l), dagen in p.mdo_dag.items():
        for dag in dagen:
            if dag not in WERKDAGEN:
                fouten.append(f"Afdeling {d} ({l}): MDO-dag '{dag}' is geen werkdag (ma, di, wo, do of vr).")
    for (e, d, groep, l, regel, uren) in p.afspraken:
        if e not in E:
            fouten.append(f"Afspraken: medewerker '{e}' bestaat niet.")
        if d is not None and groep is not None:
            fouten.append(f"Afspraken ({e}): vul óf een afdeling óf een afdelingsgroep in, niet allebei.")
        if d is None and groep is None and l is None:
            fouten.append(f"Afspraken ({e}): vul een afdeling, een afdelingsgroep of een locatie in.")
        if d is not None and d not in D:
            fouten.append(f"Afspraken ({e}): afdeling '{d}' bestaat niet.")
        if groep is not None and groep not in groepen:
            fouten.append(f"Afspraken ({e}): afdelingsgroep '{groep}' bestaat niet.")
        if l is not None and l not in L:
            fouten.append(f"Afspraken ({e}): locatie '{l}' bestaat niet.")
        elif l is not None and d is not None and (d, l) not in p.req:
            fouten.append(f"Afspraken ({e}): afdeling '{d}' bestaat niet op locatie '{l}'.")
        if regel not in REGELS:
            fouten.append(f"Afspraken ({e}): regel moet {', '.join(REGELS)} zijn.")
        if regel == "precies" and uren is None:
            fouten.append(f"Afspraken ({e}): vul bij 'precies' het aantal uren in.")
    return fouten
