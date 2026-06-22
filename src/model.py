"""Bouwt het Gurobi-model: variabelen, constraints en de lexicografische objective.

Volgt exact de wiskundige formulering: elke functie hieronder komt overeen
met een sectie daarvan (variabelen, constraint 1..7, objective stap 1..3).

Er is geen aparte kwalificatie-constraint: de enige reden dat een medewerker
een afdeling niet mag doen is via de gz_only-regel (constraint 7).
"""

import gurobipy as gp
from gurobipy import GRB

from data import Params


def get_dl_pairs(p: Params) -> list[tuple[str, str]]:
    """(afdeling, locatie)-combinaties waar daadwerkelijk vraag is (req > 0)."""
    return list(p.req.keys())


def _locations_per_department(dl_pairs):
    """d -> lijst van locaties l waarvoor (d,l) een geldige combinatie is."""
    result: dict[str, list[str]] = {}
    for d, l in dl_pairs:
        result.setdefault(d, []).append(l)
    return result


def _departments_per_location(dl_pairs):
    """l -> lijst van afdelingen d waarvoor (d,l) een geldige combinatie is."""
    result: dict[str, list[str]] = {}
    for d, l in dl_pairs:
        result.setdefault(l, []).append(d)
    return result


def build_variables(model: gp.Model, p: Params):
    """x, y, y_loc, z, w zoals gedefinieerd in de Decision Variables sectie."""

    dl_pairs = get_dl_pairs(p)

    # x[e,d,l] >= 0 : aantal uren per week dat e werkt in afdeling d, locatie l
    x = {
        (e, d, l): model.addVar(lb=0, name=f"x[{e},{d},{l}]")
        for e in p.E
        for (d, l) in dl_pairs
    }

    # y[e,d] in {0,1} : werkt e uberhaupt (>0 uur) in afdeling d
    y = {
        (e, d): model.addVar(vtype=GRB.BINARY, name=f"y[{e},{d}]")
        for e in p.E
        for d in p.D
    }

    # y_loc[e,l] in {0,1} : werkt e uberhaupt (>0 uur) op locatie l
    y_loc = {
        (e, l): model.addVar(vtype=GRB.BINARY, name=f"y_loc[{e},{l}]")
        for e in p.E
        for l in p.L
    }

    # z[e,d,l] in {0,1} : wijkt x[e,d,l] af van de huidige planning cur[e,d,l]
    z = {
        (e, d, l): model.addVar(vtype=GRB.BINARY, name=f"z[{e},{d},{l}]")
        for e in p.E
        for (d, l) in dl_pairs
    }

    # w[e] in {0,1} : werkt e zowel op p.l1 als p.l2 (het te vermijden reispaar)
    w = {e: model.addVar(vtype=GRB.BINARY, name=f"w[{e}]") for e in p.E}

    model.update()
    return x, y, y_loc, z, w


def add_coverage_constraints(model, p: Params, x, dl_pairs):
    """Constraint 1: vraagdekking. sum_e x[e,d,l] >= req[d,l] voor elke (d,l)."""
    for (d, l) in dl_pairs:
        model.addConstr(
            gp.quicksum(x[e, d, l] for e in p.E) >= p.req[d, l],
            name=f"coverage[{d},{l}]",
        )


def add_availability_constraints(model, p: Params, x, dl_pairs):
    """Constraint 2: beschikbaarheid. sum_{d,l} x[e,d,l] <= avail[e] voor elke e."""
    for e in p.E:
        model.addConstr(
            gp.quicksum(x[e, d, l] for (d, l) in dl_pairs) <= p.avail[e],
            name=f"availability[{e}]",
        )


def add_y_linking_constraints(model, p: Params, x, y, dl_pairs):
    """Constraint 3: koppelt y[e,d] aan x. sum_l x[e,d,l] <= avail[e] * y[e,d]."""
    locations_per_department = _locations_per_department(dl_pairs)
    for e in p.E:
        for d in p.D:
            locations = locations_per_department.get(d, [])
            model.addConstr(
                gp.quicksum(x[e, d, l] for l in locations) <= p.avail[e] * y[e, d],
                name=f"y_link[{e},{d}]",
            )


def add_z_linking_constraints(model, p: Params, x, z, dl_pairs):
    """Constraint 4: koppelt z[e,d,l] aan elk verschil tussen x en de huidige planning.

    Big-M: M = avail[e] + cur[e,d,l] is altijd groot genoeg, want
    x[e,d,l] <= avail[e] (constraint 2) en cur[e,d,l] is een vaste constante.
    """
    for e in p.E:
        for (d, l) in dl_pairs:
            cur_val = p.cur.get((e, d, l), 0)
            M = p.avail[e] + cur_val
            model.addConstr(
                x[e, d, l] - cur_val <= M * z[e, d, l],
                name=f"z_link_pos[{e},{d},{l}]",
            )
            model.addConstr(
                cur_val - x[e, d, l] <= M * z[e, d, l],
                name=f"z_link_neg[{e},{d},{l}]",
            )


def add_yloc_linking_constraints(model, p: Params, x, y_loc, dl_pairs):
    """Constraint 5a: koppelt y_loc[e,l] aan x. sum_d x[e,d,l] <= avail[e] * y_loc[e,l]."""
    departments_per_location = _departments_per_location(dl_pairs)
    for e in p.E:
        for l in p.L:
            departments = departments_per_location.get(l, [])
            model.addConstr(
                gp.quicksum(x[e, d, l] for d in departments) <= p.avail[e] * y_loc[e, l],
                name=f"yloc_link[{e},{l}]",
            )


def add_w_linking_constraints(model, p: Params, y_loc, w):
    """Constraint 5b: w[e] = 1 als e zowel op p.l1 als p.l2 werkt."""
    for e in p.E:
        model.addConstr(
            w[e] >= y_loc[e, p.l1] + y_loc[e, p.l2] - 1,
            name=f"w_link_lb[{e}]",
        )
        model.addConstr(w[e] <= y_loc[e, p.l1], name=f"w_link_ub1[{e}]")
        model.addConstr(w[e] <= y_loc[e, p.l2], name=f"w_link_ub2[{e}]")


def add_gz_psycholoog_constraints(model, p: Params, y_loc):
    """Constraint 6: minimaal 1 gz-psycholoog per locatie."""
    for l in p.L:
        model.addConstr(
            gp.quicksum(y_loc[e, l] for e in p.E if p.gz.get(e, 0) == 1) >= 1,
            name=f"gz_psycholoog[{l}]",
        )


def add_gz_only_constraints(model, p: Params, x, dl_pairs):
    """Constraint 7: als (d,l) gemarkeerd is als 'alleen gz-psycholoog', mag
    niemand zonder gz_e=1 daar uren maken. x[e,d,l] = 0 voor die e."""
    for (d, l) in dl_pairs:
        if p.gz_only.get((d, l), 0) != 1:
            continue
        for e in p.E:
            if p.gz.get(e, 0) == 0:
                model.addConstr(x[e, d, l] == 0, name=f"gz_only[{e},{d},{l}]")


def add_constraints(model, p: Params, x, y, y_loc, z, w, dl_pairs):
    """Voegt constraints 1 t/m 7 toe aan het model, in volgorde van de formulering."""
    add_coverage_constraints(model, p, x, dl_pairs)
    add_availability_constraints(model, p, x, dl_pairs)
    add_y_linking_constraints(model, p, x, y, dl_pairs)
    add_z_linking_constraints(model, p, x, z, dl_pairs)
    add_yloc_linking_constraints(model, p, x, y_loc, dl_pairs)
    add_w_linking_constraints(model, p, y_loc, w)
    add_gz_psycholoog_constraints(model, p, y_loc)
    add_gz_only_constraints(model, p, x, dl_pairs)
    model.update()
