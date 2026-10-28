"""Lost het model lexicografisch op: eerst minimale afwijking van de huidige
planning, dan binnen dat optimum minimale reistijd, dan binnen dat optimum
minimaal aantal medewerkers per afdeling. Komt overeen met de Objective
sectie (stap 1, 2, 3) van de wiskundige formulering.

Solver: HiGHS (open-source, via het pip-pakket highspy), aangestuurd via PuLP.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
import pulp

from data import Params
from model import CONSTRAINT_GROEPEN, Variabelen, build_model

TIJDSLIMIET_PER_STAP = 120  # seconden

# Stappen van plan(), voor de voortgangsmelding. Elke stap duurt hooguit
# TIJDSLIMIET_PER_STAP seconden.
STAPPEN = [
    "Controleren of een planning mogelijk is",
    "Zo min mogelijk wijzigingen",
    "Zo min mogelijk reizen",
    "Zo min mogelijk medewerkers per afdeling",
]
Voortgang = Callable[[int, str], None]  # (stapnummer vanaf 1, omschrijving)


def _geen_voortgang(_stap: int, _omschrijving: str) -> None:
    pass


class OnoplosbaarError(RuntimeError):
    """Het model heeft geen toegestane oplossing. `oorzaken` bevat de
    constraint-groepen (sleutels uit CONSTRAINT_GROEPEN) die, als je ze
    uitzet, het model wél oplosbaar maken."""

    def __init__(self, bericht: str, oorzaken: list[str]):
        super().__init__(bericht)
        self.oorzaken = oorzaken


def _solver(msg: bool = False, warm_start: bool = False):
    # Alle doelfuncties zijn gehele getallen (sommen van binaire variabelen).
    # Zodra gevonden oplossing en ondergrens minder dan 1 verschillen, is de
    # oplossing dus bewezen optimaal; verder zoeken is dan zinloos.
    # warm_start: begin bij de oplossing van de vorige stap. Die is ook geldig
    # voor de volgende stap; zonder dat vindt HiGHS soms lang niets.
    return pulp.HiGHS(msg=msg, gapAbs=0.999, timeLimit=TIJDSLIMIET_PER_STAP, warmStart=warm_start)


def _los_op(model: pulp.LpProblem, stap: str, msg: bool, niet_bewezen: list[str]) -> float:
    """Lost één stap op. Is de tijdslimiet bereikt maar is er wel een geldige
    planning gevonden, dan gaan we daarmee verder; de stap wordt dan genoteerd
    in `niet_bewezen` (mogelijk bestaat er een nog iets betere planning)."""
    stats = model.solve(_solver(msg, warm_start=True))
    if stats.status == pulp.LpSolveStatus.TimeLimit and stats.has_solution:
        niet_bewezen.append(stap)
    elif stats.status != pulp.LpSolveStatus.Optimal:
        raise RuntimeError(f"Stap '{stap}' niet opgelost: status {stats.status.name}")
    # Alle drie de doelfuncties zijn sommen van binaire variabelen.
    return round(pulp.value(model.objective))


def _ruim_op(p: Params, v: Variabelen, tol: float = 1e-6) -> None:
    """Ruimt de gevonden oplossing op, zodat de volgende stap vanuit een goed
    startpunt begint (warm start).

    De binaire hulpvariabelen hoeven alleen omhoog gekoppeld te zijn aan de
    uren x: y[e,d] mag 1 zijn terwijl e daar niet werkt. Tussen de stappen
    door zet de solver zulke variabelen vaak op 1, omdat ze in die stap niet
    meetellen. Hier zetten we ze terug naar wat het rooster echt zegt. Dat is
    altijd toegestaan (lagere waarden schenden geen enkele constraint) en geeft
    de volgende stap meteen een realistische beginoplossing.
    """
    werkt_afd: set[tuple[str, str]] = set()
    werkt_loc: set[tuple[str, str]] = set()
    for (e, d, l), var in v.x.items():
        uren = min(max(var.value() or 0, 0), v.ub[e, d, l])  # afrondingsruis van de solver weghalen
        if uren < tol:
            uren = 0.0
        var.setInitialValue(uren)
        if uren > tol:
            werkt_afd.add((e, d))
            werkt_loc.add((e, l))
        cur = p.cur.get((e, d, l), 0)
        v.z[e, d, l].setInitialValue(1 if abs(uren - cur) > tol else 0)
    for (e, d), var in v.y.items():
        var.setInitialValue(1 if (e, d) in werkt_afd else 0)
    for (e, l), var in v.y_loc.items():
        var.setInitialValue(1 if (e, l) in werkt_loc else 0)
    for e, var in v.w.items():
        var.setInitialValue(1 if (e, p.l1) in werkt_loc and (e, p.l2) in werkt_loc else 0)


def _som(variabelen) -> int:
    return round(sum(var.value() or 0 for var in variabelen))


def solve_lexicographic(
    model: pulp.LpProblem, p: Params, v: Variabelen, msg: bool = False, voortgang: Voortgang = _geen_voortgang
) -> dict:
    # --- Stap 1: minimaliseer afwijking van de huidige planning ---
    afwijking = pulp.lpSum(v.z[e, d, l] for e in p.E for (d, l) in v.dl_pairs)
    model.setObjective(afwijking)
    niet_bewezen: list[str] = []
    voortgang(2, STAPPEN[1])
    _los_op(model, "afwijking", msg, niet_bewezen)
    _ruim_op(p, v)
    z_star = _som(v.z.values())
    model += afwijking <= z_star, "fix_deviation"

    # --- Stap 2: minimaliseer reistijd (aantal medewerkers op zowel l1 als l2) ---
    reizen = pulp.lpSum(v.w[e] for e in p.E)
    model.setObjective(reizen)
    voortgang(3, STAPPEN[2])
    _los_op(model, "reistijd", msg, niet_bewezen)
    _ruim_op(p, v)
    w_star = _som(v.w.values())
    model += reizen <= w_star, "fix_travel"

    # --- Stap 3: minimaliseer aantal medewerkers per afdeling ---
    # AGT telt hier bewust niet mee: meerdere collega's op AGT is prima
    # (soms zelfs prettig), dus die afdeling wordt hier uitgezonderd.
    model.setObjective(pulp.lpSum(v.y[e, d] for e in p.E for d in p.D if d != "AGT"))
    voortgang(4, STAPPEN[3])
    _los_op(model, "aantal medewerkers", msg, niet_bewezen)
    _ruim_op(p, v)
    headcount = _som(var for (e, d), var in v.y.items() if d != "AGT")

    return {"deviation": z_star, "travel": w_star, "headcount": headcount, "niet_bewezen_optimaal": niet_bewezen}


def is_oplosbaar(p: Params, uitzetten: frozenset[str] = frozenset()) -> bool:
    """Heeft het model (eventueel zonder bepaalde constraint-groepen) een
    toegestane oplossing? Lost alleen een haalbaarheidsprobleem op."""
    model, _v = build_model(p, uitzetten)
    model.setObjective(0)
    return model.solve(_solver()).status == pulp.LpSolveStatus.Optimal


def diagnose(p: Params) -> list[str]:
    """Zoekt welke groepen regels samen een planning onmogelijk maken: voor
    elke groep wordt gekeken of het model zonder die groep wél oplosbaar is.
    Geeft de sleutels uit CONSTRAINT_GROEPEN terug."""
    return [g for g in CONSTRAINT_GROEPEN if is_oplosbaar(p, frozenset({g}))]


def extract_schedule(p: Params, v: Variabelen, tol: float = 1e-6) -> pd.DataFrame:
    """Zet de opgeloste x-waarden om naar een leesbare planning (uren > tol)."""
    rows = [
        {"medewerker": e, "afdeling": d, "locatie": l, "uren": round(v.x[e, d, l].value(), 2)}
        for e in p.E
        for (d, l) in v.dl_pairs
        if (v.x[e, d, l].value() or 0) > tol
    ]
    df = pd.DataFrame(rows, columns=["medewerker", "afdeling", "locatie", "uren"])
    return df.sort_values(["medewerker", "afdeling", "locatie"]).reset_index(drop=True)


@dataclass
class Resultaat:
    kpi: dict  # deviation, travel, headcount
    rooster: pd.DataFrame  # medewerker, afdeling, locatie, uren


def plan(p: Params, msg: bool = False, voortgang: Voortgang = _geen_voortgang) -> Resultaat:
    """Bouwt en lost het model op. Gooit OnoplosbaarError als er geen
    planning mogelijk is, met de vermoedelijke oorzaken erbij."""
    voortgang(1, STAPPEN[0])
    model, v = build_model(p)
    model.setObjective(0)
    status = model.solve(_solver(msg)).status
    if status == pulp.LpSolveStatus.TimeLimit:
        raise RuntimeError("De solver vond binnen de tijdslimiet geen planning.")
    if status != pulp.LpSolveStatus.Optimal:
        voortgang(1, "Geen planning mogelijk; knelpunt zoeken")
        raise OnoplosbaarError("Er is geen planning mogelijk die aan alle regels voldoet.", diagnose(p))
    kpi = solve_lexicographic(model, p, v, msg, voortgang)
    return Resultaat(kpi=kpi, rooster=extract_schedule(p, v))


def compare_headcount(p: Params, schedule: pd.DataFrame) -> pd.DataFrame:
    """Per afdeling: aantal medewerkers in de huidige planning (cur) vs. de
    nieuwe planning. Laat in 1 oogopslag zien waar het model headcount heeft
    bespaard (doel van objective-stap 3)."""
    cur_counts: dict[str, set[str]] = {}
    for (e, d, _l), uren in p.cur.items():
        if uren > 0:
            cur_counts.setdefault(d, set()).add(e)
    cur_df = pd.DataFrame(
        {"afdeling": list(cur_counts.keys()), "medewerkers_nu": [len(v) for v in cur_counts.values()]}
    )

    nieuw_df = (
        schedule.groupby("afdeling")["medewerker"]
        .nunique()
        .reset_index(name="medewerkers_nieuw")
    )

    cmp = pd.merge(cur_df, nieuw_df, on="afdeling", how="outer").fillna(0)
    cmp["medewerkers_nu"] = cmp["medewerkers_nu"].astype(int)
    cmp["medewerkers_nieuw"] = cmp["medewerkers_nieuw"].astype(int)
    cmp["verschil"] = cmp["medewerkers_nu"] - cmp["medewerkers_nieuw"]
    return cmp.sort_values("afdeling").reset_index(drop=True)


def compare_department_count_per_medewerker(p: Params, schedule: pd.DataFrame) -> pd.DataFrame:
    """Per medewerker: aantal afdelingen in de huidige planning vs. de nieuwe planning."""
    cur_afd: dict[str, set[str]] = {}
    for (e, d, _l), uren in p.cur.items():
        if uren > 0:
            cur_afd.setdefault(e, set()).add(d)
    cur_df = pd.DataFrame(
        {"medewerker": list(cur_afd.keys()), "afdelingen_nu": [len(v) for v in cur_afd.values()]}
    )

    nieuw_df = (
        schedule.groupby("medewerker")["afdeling"]
        .nunique()
        .reset_index(name="afdelingen_nieuw")
    )

    cmp = pd.merge(cur_df, nieuw_df, on="medewerker", how="outer").fillna(0)
    cmp["afdelingen_nu"] = cmp["afdelingen_nu"].astype(int)
    cmp["afdelingen_nieuw"] = cmp["afdelingen_nieuw"].astype(int)
    cmp["verschil"] = cmp["afdelingen_nu"] - cmp["afdelingen_nieuw"]
    return cmp.sort_values("medewerker").reset_index(drop=True)


def compare_travel_pair(p: Params, schedule: pd.DataFrame) -> pd.DataFrame:
    """Aantal medewerkers dat zowel op p.l1 als p.l2 werkt: huidige planning vs. nieuwe planning."""
    cur_locs: dict[str, set[str]] = {}
    for (e, _d, l), uren in p.cur.items():
        if uren > 0:
            cur_locs.setdefault(e, set()).add(l)
    aantal_nu = sum(1 for locs in cur_locs.values() if p.l1 in locs and p.l2 in locs)

    nieuw_locs = schedule.groupby("medewerker")["locatie"].apply(set)
    aantal_nieuw = sum(1 for locs in nieuw_locs if p.l1 in locs and p.l2 in locs)

    return pd.DataFrame({"moment": ["huidig", "nieuw"], "aantal_medewerkers": [aantal_nu, aantal_nieuw]})


def vergelijk_roosters(oud: pd.DataFrame, nieuw: pd.DataFrame) -> pd.DataFrame:
    """Vergelijkt twee roosters (kolommen medewerker, afdeling, locatie, uren).

    Per (medewerker, afdeling, locatie): uren_nu (oud) vs. uren_nieuw, met een
    status: nieuw / gestopt / gewijzigd / gelijk."""
    sleutel = ["medewerker", "afdeling", "locatie"]
    oud = oud[oud["uren"] > 0][sleutel + ["uren"]].rename(columns={"uren": "uren_nu"})
    nieuw = nieuw[nieuw["uren"] > 0][sleutel + ["uren"]].rename(columns={"uren": "uren_nieuw"})
    cmp = pd.merge(oud, nieuw, on=sleutel, how="outer")
    cmp[["uren_nu", "uren_nieuw"]] = cmp[["uren_nu", "uren_nieuw"]].fillna(0).astype(float)

    def status(r):
        if abs(r.uren_nu - r.uren_nieuw) < 1e-6:
            return "gelijk"
        if r.uren_nu == 0:
            return "nieuw"
        if r.uren_nieuw == 0:
            return "gestopt"
        return "gewijzigd"

    cmp["status"] = [status(r) for r in cmp.itertuples()]
    return cmp.sort_values(sleutel).reset_index(drop=True)


def huidig_rooster(p: Params) -> pd.DataFrame:
    """De huidige planning (cur) als rooster-tabel."""
    return pd.DataFrame(
        [{"medewerker": e, "afdeling": d, "locatie": l, "uren": u} for (e, d, l), u in p.cur.items() if u > 0],
        columns=["medewerker", "afdeling", "locatie", "uren"],
    )


def compare_rooster(p: Params, schedule: pd.DataFrame) -> pd.DataFrame:
    """Huidige planning vs. nieuw rooster (zie vergelijk_roosters). De regels
    met een andere status dan 'gelijk' zijn precies de toewijzingen die in
    objective-stap 1 als 'afwijking' tellen."""
    return vergelijk_roosters(huidig_rooster(p), schedule)
