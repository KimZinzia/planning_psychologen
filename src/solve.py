"""Lost het model lexicografisch op. Allereerst: zo veel mogelijk zorg dekken
(bij te weinig formatie zo min mogelijk tekort, eerlijk verdeeld, afdelingen
met voorrang zo min mogelijk gekort). Dan: basispsychologen liefst niet boven
hun maximale bezetting (zie model.add_basis_constraints). Daarna vier doelen:
1. zo min mogelijk wijzigingen ten opzichte van de huidige planning én zo
   weinig mogelijk reizigers (even belangrijk, zie REIS_GEWICHT);
2. bij afdelingen met meerdere MDO-dagen: liefst een gz-psycholoog die bij
   alle MDO's is (zie model.add_mdo_constraints);
3. de uren eerlijk verdelen: niet-gz liefst tussen hun minimum en maximum, gz rond hun
   streefwaarde, binnen elke groep zo gelijk mogelijk (in halve uren);
4. tot slot zo min mogelijk medewerkers per afdeling.

Solver: HiGHS (open-source, via het pip-pakket highspy), aangestuurd via PuLP.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
import pulp

from data import Params
from model import CONSTRAINT_GROEPEN, Variabelen, build_model

TIJDSLIMIET_PER_STAP = 300  # seconden; meer tijd = grotere kans op het allerbeste rooster

# Stappen van plan(), voor de voortgangsmelding. Elke stap duurt hooguit
# TIJDSLIMIET_PER_STAP seconden.
STAPPEN = [
    "Controleren of een planning mogelijk is",
    "Zo veel mogelijk zorg dekken",
    "Basispsychologen niet te vol",
    "Zo min mogelijk wijzigingen en reizen",
    "Gz-psycholoog bij alle MDO's",
    "Uren eerlijk verdelen",
    "Zo min mogelijk medewerkers per afdeling",
]

# Doel 1 telt wijzigingen en reizigers op. Eén reiziger telt even zwaar als
# REIS_GEWICHT wijzigingen (1 = even belangrijk). Hoger = reizen zwaarder
# vermijden, ook als dat meer wijzigingen kost. Moet een geheel getal zijn.
REIS_GEWICHT = 1

# Het laatste doel (gelijk verdelen) rekent in procentpunten bezetting. Binnen 1
# procentpunt van het best mogelijke is goed genoeg.
GAP_VERDELING = 1.0
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


def _solver(msg: bool = False, warm_start: bool = False, zoekintensiteit: float | None = None, gap: float = 0.999):
    # Alle doelfuncties zijn gehele getallen (sommen van binaire variabelen).
    # Zodra gevonden oplossing en ondergrens minder dan 1 verschillen, is de
    # oplossing dus bewezen optimaal; verder zoeken is dan zinloos.
    # warm_start: begin bij de oplossing van de vorige stap. Die is ook geldig
    # voor de volgende stap; zonder dat vindt HiGHS soms lang niets.
    # zoekintensiteit: hoeveel tijd HiGHS besteedt aan zoeken naar betere
    # roosters (standaard 0.05). Hoger helpt bij doel 4 (mensen per
    # afdeling), maar vertraagt doel 1; daarom alleen daar gebruikt.
    extra = {} if zoekintensiteit is None else {"mip_heuristic_effort": zoekintensiteit}
    # gap: voor de doelen met gehele getallen 0.999, voor het verdelen GAP_VERDELING.
    return pulp.HiGHS(msg=msg, gapAbs=gap, timeLimit=TIJDSLIMIET_PER_STAP, warmStart=warm_start, **extra)


def _los_op(
    model: pulp.LpProblem, stap: str, msg: bool, niet_bewezen: list[str], zoekintensiteit: float | None = None,
    gap: float = 0.999,
) -> float:
    """Lost één stap op. Is de tijdslimiet bereikt maar is er wel een geldige
    planning gevonden, dan gaan we daarmee verder; de stap wordt dan genoteerd
    in `niet_bewezen` (mogelijk bestaat er een nog iets betere planning)."""
    stats = model.solve(_solver(msg, warm_start=True, zoekintensiteit=zoekintensiteit, gap=gap))
    if stats.status == pulp.LpSolveStatus.TimeLimit and stats.has_solution:
        niet_bewezen.append(stap)
    elif stats.status != pulp.LpSolveStatus.Optimal:
        raise RuntimeError(f"Stap '{stap}' niet opgelost: status {stats.status.name}")
    return pulp.value(model.objective)


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
    for (d, l), var in v.tekort.items():
        gedekt = sum(min(max(v.x[e, d, l].value() or 0, 0), v.ub[e, d, l]) for e in p.E)
        tekort = min(max(p.req[d, l] - gedekt, 0.0), p.req[d, l])
        if (d, l) in v.tekort_half:  # op halve uren naar boven (meer tekort noteren mag altijd)
            tekort = min(math.ceil(2 * tekort - 1e-6) / 2, p.req[d, l])
            v.tekort_half[d, l].setInitialValue(round(2 * tekort))
        var.setInitialValue(tekort)
    for e, var in v.boven_max.items():
        uren = sum(min(max(v.x[e, d, l].value() or 0, 0), v.ub[e, d, l]) for (d, l) in v.dl_pairs)
        var.setInitialValue(max(0.0, uren - p.max_bezetting_basis * p.avail[e]))
    for (e, d, l), var in v.werkt.items():
        var.setInitialValue(1 if (v.x[e, d, l].value() or 0) > tol else 0)
    for (d, l), var in v.mdo_zonder_gz.items():
        aanwezig = any((v.x[e, d, l].value() or 0) > tol for e in v.mdo_gz_geschikt[d, l])
        var.setInitialValue(0 if aanwezig else 1)


def _som(variabelen) -> int:
    return round(sum(var.value() or 0 for var in variabelen))


def solve_lexicographic(
    model: pulp.LpProblem, p: Params, v: Variabelen, msg: bool = False, voortgang: Voortgang = _geen_voortgang
) -> dict:
    niet_bewezen: list[str] = []
    tekort = _dek_zorg(model, p, v, msg, niet_bewezen, voortgang)

    # --- Dan: basispsychologen liefst niet boven hun maximale bezetting ---
    boven_max: dict[str, float] = {}
    if v.boven_max:
        _ruim_op(p, v)
        overschrijding = pulp.lpSum(v.boven_max.values())
        model.setObjective(overschrijding)
        voortgang(3, STAPPEN[2])
        _los_op(model, "basispsychologen niet te vol", msg, niet_bewezen, gap=0.49)
        _ruim_op(p, v)
        waarde = pulp.value(overschrijding)
        model += overschrijding <= waarde + 0.01, "fix_basis"
        boven_max = {e: round(var.value() or 0, 1) for e, var in v.boven_max.items() if (var.value() or 0) > 0.05}

    # --- Doel 1: wijzigingen en reizigers (op zowel l1 als l2), even belangrijk ---
    afwijking = pulp.lpSum(v.z[e, d, l] for e in p.E for (d, l) in v.dl_pairs)
    reizen = pulp.lpSum(v.w[e] for e in p.E)
    doel1 = afwijking + REIS_GEWICHT * reizen
    model.setObjective(doel1)
    voortgang(4, STAPPEN[3])
    _los_op(model, "wijzigingen en reizen", msg, niet_bewezen)
    _ruim_op(p, v)
    z_star, w_star = _som(v.z.values()), _som(v.w.values())
    model += doel1 <= z_star + REIS_GEWICHT * w_star, "fix_doel1"

    # --- Doel 2: liefst een gz-psycholoog bij alle MDO's (alleen afdelingen met meerdere MDO-dagen) ---
    zonder_gz = 0
    if v.mdo_zonder_gz:
        mdo_doel = pulp.lpSum(v.mdo_zonder_gz.values())
        model.setObjective(mdo_doel)
        voortgang(5, STAPPEN[4])
        _los_op(model, "gz bij MDO's", msg, niet_bewezen)
        _ruim_op(p, v)
        zonder_gz = _som(v.mdo_zonder_gz.values())
        model += mdo_doel <= zonder_gz, "fix_mdo_gz"

    # --- Doel 3: uren eerlijk verdelen (niet-gz minimum, gz streefwaarde, binnen groepen gelijk) ---
    # Vóór 'mensen per afdeling': gemeten op de echte invoer sneller, en een
    # eerlijkere verdeling van de uren.
    voortgang(6, STAPPEN[5])
    _verdeel_gelijk(model, p, v, msg, niet_bewezen)

    # --- Doel 4: minimaliseer aantal medewerkers per afdeling ---
    # AGT telt hier bewust niet mee: meerdere collega's op AGT is prima
    # (soms zelfs prettig), dus die afdeling wordt hier uitgezonderd.
    aantal = pulp.lpSum(v.y[e, d] for e in p.E for d in p.D if d != "AGT")
    model.setObjective(aantal)
    voortgang(7, STAPPEN[6])
    _los_op(model, "aantal medewerkers", msg, niet_bewezen, zoekintensiteit=0.3)
    _ruim_op(p, v)
    headcount = _som(var for (e, d), var in v.y.items() if d != "AGT")
    laag, hoog = bezetting_bereik(p, extract_schedule(p, v))

    return {
        "deviation": z_star, "travel": w_star, "headcount": headcount,
        "bezetting_laag": laag, "bezetting_hoog": hoog, "mdo_zonder_gz": sorted(
            f"{d} ({l})" for (d, l), var in v.mdo_zonder_gz.items() if round(var.value() or 0) == 1
        ) if zonder_gz else [],
        "boven_max_basis": boven_max,
        "tekort": tekort,
        "niet_bewezen_optimaal": niet_bewezen,
    }


def _dek_zorg(
    model: pulp.LpProblem, p: Params, v: Variabelen, msg: bool, niet_bewezen: list[str], voortgang: Voortgang
) -> dict[str, float]:
    """Allereerste stap: zo veel mogelijk zorg dekken. Normaal is het tekort 0
    (dan is deze stap snel klaar). Is er te weinig formatie, dan:
    a. zo min mogelijk tekort in totaal;
    b. afdelingen met voorrang zo min mogelijk gekort;
    c. de overige afdelingen eerlijk: het hoogste tekort (in procenten van de
       vereiste uren) zo laag mogelijk.
    Geeft het tekort per afdeling terug ('Afdeling (locatie)' -> uren)."""
    voortgang(2, STAPPEN[1])
    _ruim_op(p, v)
    totaal = pulp.lpSum(v.tekort.values())
    model.setObjective(totaal)
    _los_op(model, "zorg dekken", msg, niet_bewezen, gap=0.01)
    _ruim_op(p, v)
    waarde = pulp.value(totaal) or 0
    if waarde <= 0.01:
        model += totaal <= 1e-6, "geen_tekort"
        return {}
    # Er is een tekort. Reken dat in halve uren (anders wordt het in snippers van
    # 0,2 uur over alle afdelingen verdeeld, en past het rooster later niet meer
    # in halve uren), en zoek het kleinste tekort opnieuw op dat raster.
    for i, ((d, l), var) in enumerate(v.tekort.items()):
        halve = model.add_variable(f"tekort_halfuur_{i}", lowBound=0, upBound=int(2 * p.req[d, l] + 1e-9), cat="Integer")
        v.tekort_half[d, l] = halve
        model += var == 0.5 * halve
    _ruim_op(p, v)
    _los_op(model, "zorg dekken", msg, niet_bewezen, gap=0.49)
    _ruim_op(p, v)
    waarde = pulp.value(totaal) or 0
    model += totaal <= waarde + 1e-4, "fix_tekort"

    voorrang = [dl for dl in v.tekort if p.voorrang.get(dl)]
    if voorrang:  # b
        som = pulp.lpSum(v.tekort[dl] for dl in voorrang)
        model.setObjective(som)
        _los_op(model, "tekort bij voorrang", msg, niet_bewezen, gap=0.01)
        _ruim_op(p, v)
        model += som <= (pulp.value(som) or 0) + 1e-4, "fix_tekort_voorrang"
    overig = [dl for dl in v.tekort if not p.voorrang.get(dl) and p.req[dl] > 0]
    if overig:  # c
        hoogste = model.add_variable("tekort_hoogste_pct", lowBound=0, upBound=100)
        for dl in overig:
            model += v.tekort[dl] * (100.0 / p.req[dl]) <= hoogste
        model.setObjective(hoogste)
        _los_op(model, "tekort eerlijk verdelen", msg, niet_bewezen, gap=0.5)
        _ruim_op(p, v)
        model += hoogste <= (hoogste.value() or 0) + 0.01, "fix_tekort_eerlijk"
    return {
        f"{d} ({l})": round(var.value() or 0, 1) for (d, l), var in v.tekort.items() if (var.value() or 0) > 0.05
    }


def _verdeel_gelijk(model: pulp.LpProblem, p: Params, v: Variabelen, msg: bool, niet_bewezen: list[str]) -> None:
    """Doel 3: de uren verdelen. Bezetting = ingeplande uren / inzetbare uren,
    in procenten. Medewerkers met 0 inzetbare uren tellen niet mee.

    In drie stappen, allemaal voorkeuren (geen harde grenzen):
    a. Medewerkers zonder gz-registratie liefst tussen p.min_bezetting_niet_gz
       en p.max_bezetting_niet_gz (bv. 75% en 85%): zo min mogelijk uren
       eronder of erboven.
    b. Gz-psychologen zo dicht mogelijk bij p.streef_bezetting_gz (bv. 90%):
       zo min mogelijk uren afwijking, naar boven of naar beneden.
    c. Daarna binnen elke groep (gz en niet-gz) zo gelijk mogelijk: het
       verschil tussen hoogste en laagste bezetting, opgeteld over beide groepen.
    Elke stap houdt de uitkomst van de vorige vast.

    De uitkomst van doel 1 en 2 blijft daarbij gelijk (die zijn vastgezet), en
    er komen geen uren bij: er wordt alleen geschoven. Omdat dat anders 'rare'
    aantallen geeft (5,8 uur), rekent deze stap in halve uren. Lukt dat niet
    (bv. door een afspraak van 4,25 uur), dan blijft de planning van doel 2 staan.

    De uitkomst wordt vastgezet, zodat doel 4 (mensen per afdeling) de verdeling
    niet meer verschuift.
    """
    medewerkers = [e for e in p.E if p.avail[e] > 0]
    if len(medewerkers) < 2:
        return
    vorige = {k: var.value() for k, var in v.x.items()}
    for i, ((e, d, l), var) in enumerate(v.x.items()):
        halve = model.add_variable(f"halfuur_{i}", lowBound=0, upBound=int(2 * v.ub[e, d, l] + 1e-9), cat="Integer")
        halve.setInitialValue(round(2 * (vorige[e, d, l] or 0)))
        model += var == 0.5 * halve
    # Gelijk verdelen mag alleen uren verschuiven, niet toevoegen. Anders is
    # 'iedereen 100%' de makkelijkste oplossing: meer uren dan de afdelingen
    # nodig hebben. (Afgerond op halve uren naar boven, zodat de planning van
    # de vorige stap altijd past.)
    totaal_voor = sum(w or 0 for w in vorige.values())
    model += pulp.lpSum(v.x.values()) <= math.ceil(2 * totaal_voor - 1e-6) / 2, "geen_extra_uren"

    def bezetting(e):
        return pulp.lpSum(v.x[e, d, l] for (d, l) in v.dl_pairs) * (100.0 / p.avail[e])

    groepen = {
        "gz": [e for e in medewerkers if p.gz.get(e, 0) == 1],
        "overig": [e for e in medewerkers if p.gz.get(e, 0) != 1],
    }
    spreiding = []
    for naam, groep in groepen.items():
        if not groep:
            continue
        hoog = model.add_variable(f"bezetting_hoog_{naam}", lowBound=0)
        laag = model.add_variable(f"bezetting_laag_{naam}", lowBound=0)
        for e in groep:
            model += bezetting(e) <= hoog
            model += bezetting(e) >= laag
        spreiding.append(hoog - laag)

    def uren(e):
        return pulp.lpSum(v.x[e, d, l] for (d, l) in v.dl_pairs)

    # a. niet-gz liefst tussen de minimale en maximale bezetting
    onder = {}
    for i, e in enumerate(groepen["overig"]):
        onder[e] = model.add_variable(f"onder_min_{i}", lowBound=0)
        model += uren(e) + onder[e] >= p.min_bezetting_niet_gz * p.avail[e]
        onder[e, "boven"] = model.add_variable(f"boven_max_ng_{i}", lowBound=0)
        model += uren(e) - p.max_bezetting_niet_gz * p.avail[e] <= onder[e, "boven"]
    # b. gz zo dicht mogelijk bij de streefbezetting
    afwijking = {}
    for i, e in enumerate(groepen["gz"]):
        afwijking[e] = model.add_variable(f"afwijking_gz_{i}", lowBound=0)
        model += uren(e) - p.streef_bezetting_gz * p.avail[e] <= afwijking[e]
        model += p.streef_bezetting_gz * p.avail[e] - uren(e) <= afwijking[e]

    try:
        for naam, termen in [("niet-gz tussen min en max", onder), ("gz rond streefwaarde", afwijking)]:
            if not termen:
                continue
            som = pulp.lpSum(termen.values())
            model.setObjective(som)
            _los_op(model, naam, msg, niet_bewezen, gap=0.49)
            _ruim_op(p, v)
            model += som <= (pulp.value(som) or 0) + 0.01, f"fix_{naam.replace(' ', '_')}"
        # c. binnen elke groep zo gelijk mogelijk
        verschil = pulp.lpSum(spreiding)
        model.setObjective(verschil)
        _los_op(model, "gelijke verdeling", msg, niet_bewezen, gap=GAP_VERDELING)
        _ruim_op(p, v)
        model += verschil <= (pulp.value(verschil) or 0) + GAP_VERDELING, "fix_verdeling"
    except RuntimeError:
        # Geen planning in halve uren gevonden: terug naar de planning van doel 2.
        for k, var in v.x.items():
            var.varValue = vorige[k]
        niet_bewezen.append("gelijke verdeling")


def bezetting_per_medewerker(p: Params, rooster: pd.DataFrame) -> pd.DataFrame:
    """Per medewerker: inzetbare uren, ingeplande uren en bezetting (%)."""
    gepland = rooster.groupby("medewerker")["uren"].sum() if len(rooster) else pd.Series(dtype=float)
    rijen = [
        {"medewerker": e, "inzetbaar": p.avail[e], "gepland": float(gepland.get(e, 0.0)),
         "bezetting": 100.0 * float(gepland.get(e, 0.0)) / p.avail[e]}
        for e in p.E if p.avail[e] > 0
    ]
    return pd.DataFrame(rijen, columns=["medewerker", "inzetbaar", "gepland", "bezetting"])


def bezetting_bereik(p: Params, rooster: pd.DataFrame) -> tuple[int, int]:
    """Laagste en hoogste bezetting (afgerond op hele procenten)."""
    b = bezetting_per_medewerker(p, rooster)["bezetting"]
    return (round(b.min()), round(b.max())) if len(b) else (0, 0)


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
