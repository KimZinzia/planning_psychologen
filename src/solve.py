"""Lost het model lexicografisch op: eerst minimale afwijking van de huidige
planning, dan binnen dat optimum minimale reistijd, dan binnen dat optimum
minimaal aantal medewerkers per afdeling. Komt overeen met de Objective
sectie (stap 1, 2, 3) van de wiskundige formulering.
"""

import gurobipy as gp
import pandas as pd
from gurobipy import GRB

from data import Params


def solve_lexicographic(model: gp.Model, p: Params, x, y, y_loc, z, w, dl_pairs) -> dict:
    # --- Stap 1: minimaliseer afwijking van de huidige planning ---
    model.setObjective(
        gp.quicksum(z[e, d, l] for e in p.E for (d, l) in dl_pairs),
        GRB.MINIMIZE,
    )
    model.optimize()
    if model.Status != GRB.OPTIMAL:
        raise RuntimeError(f"Stap 1 (afwijking) niet optimaal opgelost: status {model.Status}")
    z_star = model.ObjVal
    model.addConstr(
        gp.quicksum(z[e, d, l] for e in p.E for (d, l) in dl_pairs) <= z_star,
        name="fix_deviation",
    )

    # --- Stap 2: minimaliseer reistijd (aantal medewerkers op zowel l1 als l2) ---
    model.setObjective(gp.quicksum(w[e] for e in p.E), GRB.MINIMIZE)
    model.optimize()
    if model.Status != GRB.OPTIMAL:
        raise RuntimeError(f"Stap 2 (reistijd) niet optimaal opgelost: status {model.Status}")
    w_star = model.ObjVal
    model.addConstr(gp.quicksum(w[e] for e in p.E) <= w_star, name="fix_travel")

    # --- Stap 3: minimaliseer aantal medewerkers per afdeling ---
    model.setObjective(
        gp.quicksum(y[e, d] for e in p.E for d in p.D),
        GRB.MINIMIZE,
    )
    model.optimize()
    if model.Status != GRB.OPTIMAL:
        raise RuntimeError(f"Stap 3 (aantal medewerkers) niet optimaal opgelost: status {model.Status}")

    return {
        "deviation": z_star,
        "travel": w_star,
        "headcount": model.ObjVal,
    }


def extract_schedule(p: Params, x, dl_pairs, tol: float = 1e-6) -> pd.DataFrame:
    """Zet de opgeloste x-waarden om naar een leesbare planning (uren > tol)."""
    rows = [
        {"medewerker": e, "afdeling": d, "locatie": l, "uren": x[e, d, l].X}
        for e in p.E
        for (d, l) in dl_pairs
        if x[e, d, l].X > tol
    ]
    return pd.DataFrame(rows).sort_values(["medewerker", "afdeling", "locatie"]).reset_index(drop=True)


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
