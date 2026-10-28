"""Bouwt het optimalisatiemodel: variabelen, constraints en de lexicografische objective.

Het model gebruikt PuLP (open-source modelleertaal) en wordt opgelost met
HiGHS (open-source solver), zie solve.py. Er is dus geen commerciële licentie
nodig.

Volgt exact de wiskundige formulering: elke functie hieronder komt overeen
met een sectie daarvan (variabelen, constraint 1..12, objective stap 1..3).

Er is geen aparte kwalificatie-constraint: de enige reden dat een medewerker
een afdeling niet mag doen is via de gz_only-regel (constraint 7) — met als
uitzondering dat MinimumUren een niet-gz medewerker daar wél mag inplannen,
mits er dan ook een gz-psycholoog op diezelfde plek wordt ingepland.

Constraints 8 t/m 12 zijn optioneel en periode-specifiek: ze doen niets
tenzij je de bijbehorende gegevens invult (zie data.py).
"""

from dataclasses import dataclass, replace

import pulp

from data import Params

MINIMUM_AANWEZIGHEID = 0.5  # "uren" leeg in MinimumUren/bijsturing => op zijn minst dit aantal uur

# Groepen constraints die je (voor diagnose) los kunt uitzetten. De sleutels
# worden ook in de app getoond als uitleg waarom een planning niet kan.
CONSTRAINT_GROEPEN = {
    "dekking": "Vereiste uren per afdeling/locatie",
    "beschikbaarheid": "Inzetbare tijd per medewerker",
    "gz_per_locatie": "Minimaal 1 gz-psycholoog per locatie",
    "gz_only": "Afdelingen 'alleen gz-psycholoog'",
    "max_medewerkers": "Maximum aantal medewerkers per afdeling",
    "afdelingsgroep": "Afdelingsgroep op één locatie",
    "minimum_uren": "Minimumuren per medewerker",
    "bijsturing": "Handmatige bijsturing (vastzetten/uitsluiten)",
    "mdo": "Aanwezig op de MDO-dag van de afdeling",
}


@dataclass
class Variabelen:
    x: dict  # x[e,d,l] >= 0
    y: dict  # y[e,d] binair
    y_loc: dict  # y_loc[e,l] binair
    z: dict  # z[e,d,l] binair
    w: dict  # w[e] binair
    dl_pairs: list[tuple[str, str]]
    ub: dict  # ub[e,d,l]: bovengrens op x[e,d,l], zie _bovengrens_uren


def get_dl_pairs(p: Params) -> list[tuple[str, str]]:
    """(afdeling, locatie)-combinaties waar daadwerkelijk vraag is."""
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


def _bovengrens_uren(p: Params, dl_pairs) -> dict[tuple[str, str, str], float]:
    """Bovengrens ub[e,d,l] op het aantal uren dat e op (d,l) hoeft te werken.

    Dit is geen extra eis, maar een versterking van de formulering zodat de
    open-source solver veel sneller is. Meer uren dan
        max(req[d,l], cur[e,d,l], hoogste verplichte minimum voor e, 0.5)
    op één (afdeling, locatie) maken kan nooit nodig zijn: die uren omlaag
    zetten tot die grens houdt elke constraint geldig en maakt geen enkele
    doelfunctie slechter. Er bestaat dus altijd een optimale planning binnen
    deze grenzen, met dezelfde optimale waarden als zonder grenzen.

    De grens wordt gebruikt als big-M in de koppel-constraints (3, 4, 5a):
    hoe kleiner big-M, hoe beter de LP-relaxatie en hoe sneller het oplossen.
    """
    verplicht: dict[str, float] = {}
    for (e, _d, _groep, _l, uren) in p.min_uren:
        verplicht[e] = max(verplicht.get(e, 0), uren if uren is not None else MINIMUM_AANWEZIGHEID)
    for (e, _d, _l, actie, uren) in p.bijsturing:
        if actie == "vastzetten":
            verplicht[e] = max(verplicht.get(e, 0), uren if uren is not None else MINIMUM_AANWEZIGHEID)

    return {
        (e, d, l): min(
            p.avail[e],
            max(p.req[d, l], p.cur.get((e, d, l), 0), verplicht.get(e, 0), MINIMUM_AANWEZIGHEID),
        )
        for e in p.E
        for (d, l) in dl_pairs
    }


def build_variables(model: pulp.LpProblem, p: Params) -> Variabelen:
    """x, y, y_loc, z, w zoals gedefinieerd in de Decision Variables sectie.

    Variabelen krijgen een naam op basis van indexnummers (x_3_12): namen van
    afdelingen bevatten tekens als '/' en spaties die PuLP niet toestaat.
    """
    dl_pairs = get_dl_pairs(p)
    ie = {e: i for i, e in enumerate(p.E)}
    idl = {dl: i for i, dl in enumerate(dl_pairs)}
    id_ = {d: i for i, d in enumerate(p.D)}
    il = {l: i for i, l in enumerate(p.L)}

    ub = _bovengrens_uren(p, dl_pairs)

    # x[e,d,l] >= 0 : aantal uren per week dat e werkt in afdeling d, locatie l
    x = {
        (e, d, l): model.add_variable(f"x_{ie[e]}_{idl[d, l]}", lowBound=0, upBound=ub[e, d, l])
        for e in p.E
        for (d, l) in dl_pairs
    }
    # y[e,d] in {0,1} : werkt e uberhaupt (>0 uur) in afdeling d
    y = {(e, d): model.add_variable(f"y_{ie[e]}_{id_[d]}", cat="Binary") for e in p.E for d in p.D}
    # y_loc[e,l] in {0,1} : werkt e uberhaupt (>0 uur) op locatie l
    y_loc = {(e, l): model.add_variable(f"yloc_{ie[e]}_{il[l]}", cat="Binary") for e in p.E for l in p.L}
    # z[e,d,l] in {0,1} : wijkt x[e,d,l] af van de huidige planning cur[e,d,l]
    z = {(e, d, l): model.add_variable(f"z_{ie[e]}_{idl[d, l]}", cat="Binary") for e in p.E for (d, l) in dl_pairs}
    # w[e] in {0,1} : werkt e zowel op p.l1 als p.l2 (het te vermijden reispaar)
    w = {e: model.add_variable(f"w_{ie[e]}", cat="Binary") for e in p.E}

    return Variabelen(x=x, y=y, y_loc=y_loc, z=z, w=w, dl_pairs=dl_pairs, ub=ub)


def add_coverage_constraints(model, p: Params, v: Variabelen):
    """Constraint 1: vraagdekking. sum_e x[e,d,l] >= req[d,l] voor elke (d,l)."""
    for (d, l) in v.dl_pairs:
        model += pulp.lpSum(v.x[e, d, l] for e in p.E) >= p.req[d, l]


def add_availability_constraints(model, p: Params, v: Variabelen):
    """Constraint 2: beschikbaarheid. sum_{d,l} x[e,d,l] <= avail[e] voor elke e."""
    for e in p.E:
        model += pulp.lpSum(v.x[e, d, l] for (d, l) in v.dl_pairs) <= p.avail[e]


def add_y_linking_constraints(model, p: Params, v: Variabelen):
    """Constraint 3: koppelt y[e,d] aan x: x[e,d,l] <= ub[e,d,l] * y[e,d].

    Per (afdeling, locatie) uitgeschreven in plaats van één som met
    avail[e] als big-M: zelfde betekenis, maar een veel sterkere formulering."""
    for e in p.E:
        for (d, l) in v.dl_pairs:
            model += v.x[e, d, l] <= v.ub[e, d, l] * v.y[e, d]


def add_z_linking_constraints(model, p: Params, v: Variabelen):
    """Constraint 4: koppelt z[e,d,l] aan elk verschil tussen x en de huidige planning.

    Big-M per richting zo klein mogelijk (sterkere formulering):
    - meer uren dan nu: x - cur kan hooguit ub[e,d,l] - cur worden;
    - minder uren dan nu: cur - x kan hooguit cur worden (want x >= 0).
    """
    for e in p.E:
        for (d, l) in v.dl_pairs:
            cur_val = p.cur.get((e, d, l), 0)
            m_omhoog = max(v.ub[e, d, l] - cur_val, 0)
            if m_omhoog > 0:
                model += v.x[e, d, l] - cur_val <= m_omhoog * v.z[e, d, l]
            if cur_val > 0:
                model += cur_val - v.x[e, d, l] <= cur_val * v.z[e, d, l]


def add_yloc_linking_constraints(model, p: Params, v: Variabelen):
    """Constraint 5a: koppelt y_loc[e,l] in beide richtingen aan x.

    - Werkt e uren op l, dan y_loc[e,l] = 1:
      x[e,d,l] <= ub[e,d,l] * y_loc[e,l] (per afdeling uitgeschreven, zie
      add_y_linking_constraints).
    - Is y_loc[e,l] = 1, dan werkt e daar ook echt minstens
      MINIMUM_AANWEZIGHEID uur: sum_d x[e,d,l] >= MINIMUM_AANWEZIGHEID * y_loc[e,l].
      Zonder deze richting kon het model y_loc op 1 zetten zonder uren in te
      plannen, waardoor 'minimaal 1 gz-psycholoog per locatie' (constraint 6)
      op papier klopte maar in het rooster niet.
    """
    departments_per_location = _departments_per_location(v.dl_pairs)
    for e in p.E:
        for (d, l) in v.dl_pairs:
            model += v.x[e, d, l] <= v.ub[e, d, l] * v.y_loc[e, l]
        for l in p.L:
            departments = departments_per_location.get(l, [])
            model += pulp.lpSum(v.x[e, d, l] for d in departments) >= MINIMUM_AANWEZIGHEID * v.y_loc[e, l]


def add_w_linking_constraints(model, p: Params, v: Variabelen):
    """Constraint 5b: w[e] = 1 als e zowel op p.l1 als p.l2 werkt."""
    for e in p.E:
        model += v.w[e] >= v.y_loc[e, p.l1] + v.y_loc[e, p.l2] - 1
        model += v.w[e] <= v.y_loc[e, p.l1]
        model += v.w[e] <= v.y_loc[e, p.l2]


def add_gz_psycholoog_constraints(model, p: Params, v: Variabelen):
    """Constraint 6: minimaal 1 gz-psycholoog per locatie."""
    for l in p.L:
        model += pulp.lpSum(v.y_loc[e, l] for e in p.E if p.gz.get(e, 0) == 1) >= 1


def _min_uren_target_dl(p: Params, dl_pairs, d, groep, l):
    """Vertaalt één MinimumUren-rij (afdeling of afdelingsgroep, optioneel
    locatie) naar de (afdeling, locatie)-paren waar hij op slaat."""
    if d is not None:
        target = [(dep, loc) for (dep, loc) in dl_pairs if dep == d]
    else:
        target = [(dep, loc) for (dep, loc) in dl_pairs if p.afdelingsgroep.get(dep) == groep]
    if l is not None:
        target = [(dep, loc) for (dep, loc) in target if loc == l]
    return target


def add_gz_only_constraints(model, p: Params, v: Variabelen):
    """Constraint 7: als (d,l) gemarkeerd is als 'alleen gz-psycholoog', mag
    niemand zonder gz_e=1 daar uren maken — tenzij MinimumUren (of een
    handmatige vastzetting) die medewerker daar juist verplicht stelt. In dat
    geval mag het wel, maar dan moet er op diezelfde (afdeling, locatie) ook
    een gz-psycholoog worden ingepland (met z'n tweeën)."""
    exempted: set[tuple[str, str, str]] = set()
    for (e, d, groep, l, _uren) in p.min_uren:
        for (dep, loc) in _min_uren_target_dl(p, v.dl_pairs, d, groep, l):
            exempted.add((e, dep, loc))
    for (e, d, l, actie, _uren) in p.bijsturing:
        if actie == "vastzetten":
            for (dep, loc) in _min_uren_target_dl(p, v.dl_pairs, d, None, l):
                exempted.add((e, dep, loc))

    needs_buddy: set[tuple[str, str]] = set()
    for (d, l) in v.dl_pairs:
        if p.gz_only.get((d, l), 0) != 1:
            continue
        for e in p.E:
            if p.gz.get(e, 0) == 1:
                continue
            if (e, d, l) in exempted:
                needs_buddy.add((d, l))
            else:
                model += v.x[e, d, l] == 0

    for (d, l) in needs_buddy:
        model += pulp.lpSum(v.x[e, d, l] for e in p.E if p.gz.get(e, 0) == 1) >= MINIMUM_AANWEZIGHEID


def add_max_medewerkers_constraints(model, p: Params, v: Variabelen):
    """Constraint 8 (optioneel): headcount-cap per afdeling, bv. max 1
    medewerker op Lotus 2. Doet niets voor afdelingen zonder max_medewerkers."""
    for d, cap in p.max_medewerkers.items():
        model += pulp.lpSum(v.y[e, d] for e in p.E) <= cap


def add_afdelingsgroep_constraints(model, p: Params, v: Variabelen):
    """Constraint 9 (optioneel): afdelingen met hetzelfde afdelingsgroep-label
    (bv. "GRZ") horen bij elkaar — een medewerker die in die groep werkt,
    moet dat op precies één locatie doen. Doet niets voor afdelingen zonder
    afdelingsgroep, of groepen die toch al maar op 1 locatie voorkomen."""
    groepen: dict[str, list[tuple[str, str]]] = {}
    for (d, l) in v.dl_pairs:
        groep = p.afdelingsgroep.get(d)
        if groep is not None:
            groepen.setdefault(groep, []).append((d, l))

    ie = {e: i for i, e in enumerate(p.E)}
    for gi, (groep, group_dl_pairs) in enumerate(sorted(groepen.items())):
        locations = sorted({l for (_d, l) in group_dl_pairs})
        if len(locations) < 2:
            continue
        departments_per_location = _departments_per_location(group_dl_pairs)

        y_group = {
            (e, l): model.add_variable(f"ygroup_{gi}_{ie[e]}_{li}", cat="Binary")
            for e in p.E
            for li, l in enumerate(locations)
        }
        for e in p.E:
            for l in locations:
                for d in departments_per_location[l]:
                    model += v.x[e, d, l] <= v.ub[e, d, l] * y_group[e, l]
            model += pulp.lpSum(y_group[e, l] for l in locations) <= 1


def add_minimum_uren_constraints(model, p: Params, v: Variabelen):
    """Constraint 10 (optioneel): verplicht minimumaantal uren voor een
    medewerker op een afdeling óf een afdelingsgroep, desgewenst toegespitst
    op een locatie. Uren leeg => moet er zijn, aantal maakt niet uit; dat
    wordt hier vertaald naar MINIMUM_AANWEZIGHEID uur."""
    for (e, d, groep, l, uren) in p.min_uren:
        target_dl = _min_uren_target_dl(p, v.dl_pairs, d, groep, l)
        minimum = uren if uren is not None else MINIMUM_AANWEZIGHEID
        model += pulp.lpSum(v.x[e, dep, loc] for (dep, loc) in target_dl) >= minimum


def add_bijsturing_constraints(model, p: Params, v: Variabelen):
    """Constraint 11 (optioneel): handmatige bijsturing vanuit de app.
    - 'uitsluiten': medewerker e mag niet op afdeling d werken (op locatie l,
      of op alle locaties als l leeg is).
    - 'vastzetten': medewerker e moet daar precies 'uren' uur werken; uren
      leeg => moet er zijn, aantal maakt niet uit (>= MINIMUM_AANWEZIGHEID).
    """
    for (e, d, l, actie, uren) in p.bijsturing:
        target_dl = _min_uren_target_dl(p, v.dl_pairs, d, None, l)
        totaal = pulp.lpSum(v.x[e, dep, loc] for (dep, loc) in target_dl)
        if actie == "uitsluiten":
            model += totaal == 0
        elif actie == "vastzetten":
            if uren is None:
                model += totaal >= MINIMUM_AANWEZIGHEID
            else:
                model += totaal == uren
        else:
            raise ValueError(f"Onbekende bijsturing-actie: {actie!r}")


def add_mdo_constraints(model, p: Params, v: Variabelen):
    """Constraint 12 (optioneel): wie op een afdeling werkt, moet werken op de
    dag van het MDO van die afdeling. Een medewerker die op die dag niet werkt,
    kan daar dus niet ingepland worden: x[e,d,l] = 0.

    Medewerkers zonder ingevulde werkdagen gelden als 'onbekend' en worden
    niet beperkt. Afdelingen zonder MDO-dag ook niet.
    """
    for (d, l), dag in p.mdo_dag.items():
        if (d, l) not in v.dl_pairs:
            continue
        for e in p.E:
            dagen = p.werkdagen.get(e, set())
            if dagen and dag not in dagen:
                model += v.x[e, d, l] == 0


def build_model(p: Params, uitzetten: frozenset[str] = frozenset()) -> tuple[pulp.LpProblem, Variabelen]:
    """Bouwt het volledige model (constraints 1 t/m 11, in volgorde van de formulering).

    `uitzetten` is een set sleutels uit CONSTRAINT_GROEPEN die worden
    overgeslagen; alleen bedoeld voor de diagnose bij een onoplosbaar model.
    De koppel-constraints (3, 4, 5) staan altijd aan: die definiëren de
    variabelen en zijn nooit de oorzaak van onoplosbaarheid.
    """
    if "beschikbaarheid" in uitzetten:
        # De koppel-constraints gebruiken avail[e] als big-M; zonder
        # beschikbaarheidsgrens is de totale vraag een veilige bovengrens.
        ruim = sum(p.req.values()) + 1
        p = replace(p, avail={e: ruim for e in p.E})

    model = pulp.LpProblem("planning", pulp.LpMinimize)
    v = build_variables(model, p)

    stappen = [
        ("dekking", add_coverage_constraints),
        ("beschikbaarheid", add_availability_constraints),
        (None, add_y_linking_constraints),
        (None, add_z_linking_constraints),
        (None, add_yloc_linking_constraints),
        (None, add_w_linking_constraints),
        ("gz_per_locatie", add_gz_psycholoog_constraints),
        ("gz_only", add_gz_only_constraints),
        ("max_medewerkers", add_max_medewerkers_constraints),
        ("afdelingsgroep", add_afdelingsgroep_constraints),
        ("minimum_uren", add_minimum_uren_constraints),
        ("bijsturing", add_bijsturing_constraints),
        ("mdo", add_mdo_constraints),
    ]
    for groep, functie in stappen:
        if groep not in uitzetten:
            functie(model, p, v)
    return model, v
