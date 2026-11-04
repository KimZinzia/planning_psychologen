"""Bouwt het optimalisatiemodel: variabelen, constraints en de lexicografische objective.

Het model gebruikt PuLP (open-source modelleertaal) en wordt opgelost met
HiGHS (open-source solver), zie solve.py. Er is dus geen commerciële licentie
nodig.

Volgt exact de wiskundige formulering: elke functie hieronder komt overeen
met een sectie daarvan (variabelen, constraint 1..12, objective doel 1..3).

Er is geen aparte kwalificatie-constraint: de enige reden dat een medewerker
een afdeling niet mag doen is via de gz_only-regel (constraint 7) — met als
uitzondering dat een afspraak (minimaal/precies) een niet-gz medewerker daar wél mag inplannen,
mits er dan ook een gz-psycholoog op diezelfde plek wordt ingepland.

Constraints 8 t/m 12 zijn optioneel en periode-specifiek: ze doen niets
tenzij je de bijbehorende gegevens invult (zie data.py).
"""

from dataclasses import dataclass, field, replace

import pulp

from data import Params, ido_overlapt, is_basispsycholoog

# Minimaal aantal uren van een toewijzing (één medewerker op één afdeling op
# één locatie): wie ergens werkt, werkt daar minstens zoveel uur. Ook gebruikt
# voor een afspraak 'minimaal' zonder uren ('moet er werken').
MINIMUM_AANWEZIGHEID = 1.0

# Groepen constraints die je (voor diagnose) los kunt uitzetten. De sleutels
# worden ook in de app getoond als uitleg waarom een planning niet kan.
CONSTRAINT_GROEPEN = {
    "dekking": "Vereiste uren per afdeling/locatie",
    "beschikbaarheid": "Inzetbare tijd per medewerker",
    "gz_per_locatie": "Minimaal 1 gz-psycholoog per locatie",
    "gz_only": "Afdelingen 'alleen gz-psycholoog'",
    "max_medewerkers": "Maximum aantal medewerkers per afdeling",
    "afdelingsgroep": "Afdelingsgroep op één locatie",
    "afspraken": "Afspraken per medewerker (minimaal, precies, uitsluiten)",
    "minimum_toewijzing": "Minimaal 1 uur per toewijzing",
    "mdo": "Aanwezig bij de IDO's van de afdeling (niet twee tegelijk)",
    "nooit_samen": "Locaties die nooit gecombineerd mogen worden",
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
    # mdo_zonder_gz[d,l] binair: 1 = bij deze afdeling (met meerdere MDO-dagen)
    # is geen gz-psycholoog die bij álle MDO's kan zijn. Gevuld door add_mdo_constraints.
    mdo_zonder_gz: dict = field(default_factory=dict)
    mdo_gz_geschikt: dict = field(default_factory=dict)
    # ido[e,d,l,k] binair: e is bij IDO nummer k van afdeling d op locatie l (zie add_mdo_constraints)
    ido: dict = field(default_factory=dict)
    # ido_gz_alle[e,d,l] binair: gz-psycholoog e is bij alle IDO's van d op l
    ido_gz_alle: dict = field(default_factory=dict)
    # werkt[e,d,l] binair, alleen voor afdelingen op meerdere locaties (zie add_minimum_toewijzing_constraints)
    werkt: dict = field(default_factory=dict)
    # boven_max[e] >= 0: uren dat basispsycholoog e boven de maximale bezetting zit (zie add_basis_constraints)
    boven_max: dict = field(default_factory=dict)
    # tekort[d,l] >= 0: uren die afdeling d op locatie l tekortkomt (zie add_coverage_constraints)
    tekort: dict = field(default_factory=dict)
    tekort_half: dict = field(default_factory=dict)  # alleen bij een tekort: tekort = 0,5 * tekort_half (zie solve._dek_zorg)
    # teveel_afd[e] >= 0: aantal afdelingen dat e boven p.max_afdelingen zit (zie solve._werkdruk)
    teveel_afd: dict = field(default_factory=dict)


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
    for (e, _d, _groep, _l, regel, uren) in p.afspraken:
        if regel in ("minimaal", "precies"):
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
    """Constraint 1: vraagdekking. sum_e x[e,d,l] + tekort[d,l] >= req[d,l].

    tekort[d,l] >= 0 is het aantal uren dat afdeling d op locatie l tekortkomt.
    Normaal is dat 0; solve.py maakt het tekort als allereerste zo klein
    mogelijk. Zo is er bij te weinig formatie tóch een rooster, waarin het
    tekort zo klein en eerlijk mogelijk verdeeld is (zie solve.py).
    """
    for i, (d, l) in enumerate(v.dl_pairs):
        tekort = model.add_variable(f"tekort_{i}", lowBound=0, upBound=p.req[d, l])
        v.tekort[d, l] = tekort
        model += pulp.lpSum(v.x[e, d, l] for e in p.E) + tekort >= p.req[d, l]


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


def add_versterkingen(model, p: Params, v: Variabelen):
    """Geen extra eis, maar een versterking zodat de solver sneller rekent.

    Werkt e nu al uren op (d,l) en blijft dat ongewijzigd (z = 0), dan werkt
    e daar dus nog steeds: y[e,d] >= 1 - z[e,d,l] en y_loc[e,l] >= 1 - z[e,d,l].
    Elke geldige planning voldoet hier al aan, maar zonder deze regels 'ziet'
    de solver dat pas laat. Halveert de rekentijd van stap 1 en zorgt dat
    stap 2 bewezen optimaal wordt (gemeten op de echte invoer).
    """
    for (e, d, l), cur in p.cur.items():
        if cur > 0 and (e, d, l) in v.x:
            model += v.y[e, d] >= 1 - v.z[e, d, l]
            model += v.y_loc[e, l] >= 1 - v.z[e, d, l]


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


def add_minimum_toewijzing_constraints(model, p: Params, v: Variabelen):
    """Wie op een afdeling werkt, werkt daar minstens MINIMUM_AANWEZIGHEID uur:
    x[e,d,l] = 0 of x[e,d,l] >= MINIMUM_AANWEZIGHEID.

    Voor een afdeling op één locatie is y[e,d] precies 'werkt e hier', dus
    x >= minimum * y. Een afdeling op meerdere locaties (bv. AGT) krijgt per
    locatie een eigen ja/nee-variabele.
    """
    locaties = _locations_per_department(v.dl_pairs)
    ie = {e: i for i, e in enumerate(p.E)}
    idl = {dl: i for i, dl in enumerate(v.dl_pairs)}
    for e in p.E:
        for (d, l) in v.dl_pairs:
            if len(locaties[d]) == 1:
                model += v.x[e, d, l] >= MINIMUM_AANWEZIGHEID * v.y[e, d]
            else:
                werkt = model.add_variable(f"werkt_{ie[e]}_{idl[d, l]}", cat="Binary")
                v.werkt[e, d, l] = werkt
                model += v.x[e, d, l] <= v.ub[e, d, l] * werkt
                model += v.x[e, d, l] >= MINIMUM_AANWEZIGHEID * werkt


def add_basis_constraints(model, p: Params, v: Variabelen):
    """Basispsychologen (geen gz, niet in opleiding) liefst niet voller dan
    max_bezetting_basis van hun inzetbare uren. Geen harde eis: boven_max[e]
    meet hoeveel uur e erboven zit, en solve.py maakt dat zo klein mogelijk.
    """
    ie = {e: i for i, e in enumerate(p.E)}
    for e in p.E:
        if p.avail[e] > 0 and is_basispsycholoog(p, e):
            boven = model.add_variable(f"boven_max_{ie[e]}", lowBound=0)
            v.boven_max[e] = boven
            model += pulp.lpSum(v.x[e, d, l] for (d, l) in v.dl_pairs) - p.max_bezetting_basis * p.avail[e] <= boven


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
    """Vertaalt één afspraak (afdeling, afdelingsgroep of geen van beide,
    optioneel locatie) naar de (afdeling, locatie)-paren waar hij op slaat.
    Geen afdeling en geen groep = alle afdelingen (op locatie l)."""
    if d is not None:
        target = [(dep, loc) for (dep, loc) in dl_pairs if dep == d]
    elif groep is not None:
        target = [(dep, loc) for (dep, loc) in dl_pairs if p.afdelingsgroep.get(dep) == groep]
    else:
        target = list(dl_pairs)
    if l is not None:
        target = [(dep, loc) for (dep, loc) in target if loc == l]
    return target


def add_gz_only_constraints(model, p: Params, v: Variabelen):
    """Constraint 7: als (d,l) gemarkeerd is als 'alleen gz-psycholoog', mag
    niemand zonder gz_e=1 daar uren maken — tenzij een afspraak (minimaal of
    precies) die medewerker daar juist verplicht stelt. In dat geval mag het
    wel, maar dan moet er op diezelfde (afdeling, locatie) ook een
    gz-psycholoog worden ingepland (met z'n tweeën). Een afspraak voor een hele
    locatie geeft deze uitzondering niet: die noemt geen specifieke afdeling."""
    exempted: set[tuple[str, str, str]] = set()
    for (e, d, groep, l, regel, _uren) in p.afspraken:
        if regel in ("minimaal", "precies") and (d is not None or groep is not None):
            for (dep, loc) in _min_uren_target_dl(p, v.dl_pairs, d, groep, l):
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


def add_afspraken_constraints(model, p: Params, v: Variabelen):
    """Constraint 10 (optioneel): afspraken per medewerker, voor een afdeling,
    een afdelingsgroep of een hele locatie (eventueel toegespitst op een locatie).
    - 'minimaal':   minstens 'uren' uur; uren leeg => moet er werken, aantal
                    maakt niet uit (>= MINIMUM_AANWEZIGHEID);
    - 'precies':    precies 'uren' uur;
    - 'uitsluiten': daar geen uren.
    """
    for (e, d, groep, l, regel, uren) in p.afspraken:
        target_dl = _min_uren_target_dl(p, v.dl_pairs, d, groep, l)
        totaal = pulp.lpSum(v.x[e, dep, loc] for (dep, loc) in target_dl)
        if regel == "minimaal":
            model += totaal >= (uren if uren is not None else MINIMUM_AANWEZIGHEID)
        elif regel == "precies":
            model += totaal == uren
        elif regel == "uitsluiten":
            model += totaal == 0
        elif regel == "geen_ido":
            continue  # geldt bij de IDO's, zie add_mdo_constraints
        else:
            raise ValueError(f"Onbekende regel in afspraken: {regel!r}")


def _geen_ido(p: Params, v: Variabelen) -> set[tuple[str, str, str]]:
    """(e, d, l) waarvoor een afspraak 'geen_ido' geldt: e hoeft daar niet bij de IDO."""
    vrij = set()
    for (e, d, groep, l, regel, _uren) in p.afspraken:
        if regel == "geen_ido":
            vrij |= {(e, dep, loc) for (dep, loc) in _min_uren_target_dl(p, v.dl_pairs, d, groep, l)}
    return vrij


def add_mdo_constraints(model, p: Params, v: Variabelen):
    """Constraint 11 (optioneel): IDO's (in de code nog 'mdo'). Per afdeling
    met IDO's (p.ido: dag, weken, van, tot). ido[e,d,l,k] = 1: e is bij IDO k
    van afdeling d op locatie l. Dat kan alleen als e op die dag werkt en daar
    uren heeft.

    a. Wie op de afdeling werkt, is bij minstens één IDO van die afdeling.
       Wie op geen enkele IDO-dag werkt, kan er dus niet ingepland worden.
       Uitzondering: een afspraak 'geen_ido' (e hoeft niet bij de IDO).
    b. Bij elke IDO is minstens één psycholoog van de afdeling.
    c. Niemand is bij twee IDO's tegelijk: zelfde dag, weken die samenvallen
       (elke week valt samen met even en oneven) en tijden die overlappen.
    d. Voorkeur (geen eis): bij afdelingen met meerdere IDO's liefst een
       gz-psycholoog die bij álle IDO's is. mdo_zonder_gz[d,l] = 1 als dat
       niet lukt; het aantal daarvan wordt in solve.py zo klein mogelijk gemaakt.

    Medewerkers zonder ingevulde werkdagen gelden als 'onbekend' en worden
    niet beperkt door de dag. Afdelingen zonder IDO ook niet.
    """
    vrij = _geen_ido(p, v)
    ie = {e: i for i, e in enumerate(p.E)}
    alle_slots = []  # (d, l, k, slot)
    for n, ((d, l), slots) in enumerate(p.ido.items()):
        if (d, l) not in v.dl_pairs or p.req[d, l] <= 0:
            continue  # bv. tijdelijk 0 uur (noodplanning): dan ook geen IDO-eisen
        bij = {}
        for e in p.E:
            werk = p.werkdagen.get(e, set())
            for k, slot in enumerate(slots):
                if not werk or slot[0] in werk:
                    var = model.add_variable(f"ido_{ie[e]}_{n}_{k}", cat="Binary")
                    v.ido[e, d, l, k] = var
                    bij[e, k] = var
                    model += MINIMUM_AANWEZIGHEID * var <= v.x[e, d, l]
            eigen = [bij[e, k] for k in range(len(slots)) if (e, k) in bij]
            if (e, d, l) in vrij:
                continue
            if not eigen:  # a: kan bij geen enkele IDO zijn
                model += v.x[e, d, l] == 0
                continue
            werkt = v.werkt.get((e, d, l), v.y[e, d])
            model += werkt <= pulp.lpSum(eigen)  # a
        for k, slot in enumerate(slots):  # b
            model += pulp.lpSum(bij[e, k] for e in p.E if (e, k) in bij) >= 1
            alle_slots.append((d, l, k, slot))
        if len(slots) < 2:
            continue
        # d: gz-psycholoog die bij alle IDO's is
        geschikt = [e for e in p.E if p.gz.get(e, 0) == 1 and all((e, k) in bij for k in range(len(slots)))]
        straf = model.add_variable(f"mdo_zonder_gz_{len(v.mdo_zonder_gz)}", cat="Binary")
        v.mdo_zonder_gz[d, l] = straf
        v.mdo_gz_geschikt[d, l] = geschikt
        alles = []
        for e in geschikt:
            g = model.add_variable(f"ido_gz_alle_{ie[e]}_{n}", cat="Binary")
            v.ido_gz_alle[e, d, l] = g
            alles.append(g)
            for k in range(len(slots)):
                model += g <= bij[e, k]
        model += pulp.lpSum(alles) >= 1 - straf

    # c: niet bij twee IDO's tegelijk. Per medewerker, per paar IDO's dat botst
    # (zie data.ido_overlapt; een gezamenlijk overleg botst niet).
    botsend = [
        (i, j) for i in range(len(alle_slots)) for j in range(i + 1, len(alle_slots))
        if ido_overlapt(alle_slots[i][3], alle_slots[j][3])
    ]
    for e in p.E:
        for i, j in botsend:
            (d1, l1, k1, _), (d2, l2, k2, _) = alle_slots[i], alle_slots[j]
            if (e, d1, l1, k1) in v.ido and (e, d2, l2, k2) in v.ido:
                model += v.ido[e, d1, l1, k1] + v.ido[e, d2, l2, k2] <= 1


def add_nooit_samen_constraints(model, p: Params, v: Variabelen):
    """Constraint 12 (optioneel): locatieparen die één medewerker nooit mag
    combineren (bv. ONO en RK): y_loc[e,a] + y_loc[e,b] <= 1.

    Dit is een harde eis, anders dan het reispaar l1/l2 (doel 1 van de
    doelfunctie), dat alleen 'liever niet' is."""
    for a, b in p.nooit_samen:
        if a in p.L and b in p.L and a != b:
            for e in p.E:
                model += v.y_loc[e, a] + v.y_loc[e, b] <= 1


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
        ("minimum_toewijzing", add_minimum_toewijzing_constraints),
        (None, add_basis_constraints),
        ("gz_per_locatie", add_gz_psycholoog_constraints),
        ("gz_only", add_gz_only_constraints),
        ("max_medewerkers", add_max_medewerkers_constraints),
        ("afdelingsgroep", add_afdelingsgroep_constraints),
        ("afspraken", add_afspraken_constraints),
        ("mdo", add_mdo_constraints),
        ("nooit_samen", add_nooit_samen_constraints),
        # Bewust als laatste: HiGHS is gevoelig voor de volgorde van regels en
        # rekent op deze manier het snelst (gemeten op de echte invoer).
        (None, add_versterkingen),
    ]
    for groep, functie in stappen:
        if groep not in uitzetten:
            functie(model, p, v)
    return model, v
