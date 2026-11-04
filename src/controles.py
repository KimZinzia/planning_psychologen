"""Controles op de invoer, vóór het plannen: geeft 'aandachtspunten' terug die
de app bovenaan de pagina's toont.

Drie niveaus:
- fout:         er is zeker geen planning mogelijk (of de invoer klopt niet);
- waarschuwing: planning is mogelijk, maar krap of waarschijnlijk niet bedoeld;
- info:         goed om te weten, geen actie nodig.

Alle controles zijn snel (geen solver nodig).
"""

from dataclasses import dataclass, field

import pandas as pd

from data import DAG_NAMEN, Params, ido_overlapt, ido_tekst, params_from_tables, valideer
from model import MINIMUM_AANWEZIGHEID, _min_uren_target_dl

RUIMTE_KRAP = 0.05  # minder dan 5% ruimte tussen inzetbaar en vereist = krap


@dataclass
class Signaal:
    niveau: str  # "fout", "waarschuwing" of "info"
    tekst: str
    details: list[str] = field(default_factory=list)


def _uren(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def signalen(tabellen: dict[str, pd.DataFrame]) -> list[Signaal]:
    p = params_from_tables(tabellen)
    result: list[Signaal] = []

    fouten = valideer(p)
    if fouten:
        result.append(Signaal("fout", "De invoer bevat fouten die eerst opgelost moeten worden.", fouten))
        return result  # de andere controles gaan uit van geldige invoer

    result += _capaciteit(p)
    result += _gz(p)
    result += _mdo(p)
    result += _afspraken(p)
    result += _huidige_planning(p)
    result += _werkdagen(p)
    result += _nooit_samen(p)
    result += _altijd_samen(p)
    volgorde = {"fout": 0, "waarschuwing": 1, "info": 2}
    return sorted(result, key=lambda s: volgorde[s.niveau])


def _capaciteit(p: Params) -> list[Signaal]:
    vereist, inzetbaar = sum(p.req.values()), sum(p.avail.values())
    ruimte = inzetbaar - vereist
    if ruimte < 0:
        return [Signaal(
            "waarschuwing",
            f"Er is {_uren(-ruimte)} uur per week te weinig: {_uren(inzetbaar)} uur inzetbaar, {_uren(vereist)} uur vereist. "
            "De planning verdeelt het tekort zo eerlijk mogelijk (afdelingen met voorrang zo min mogelijk). "
            "Zie ook de pagina Noodplanning.",
        )]
    if ruimte < RUIMTE_KRAP * vereist:
        return [Signaal(
            "waarschuwing",
            f"Nog maar {_uren(ruimte)} uur per week ruimte ({_uren(inzetbaar)} inzetbaar, {_uren(vereist)} vereist). "
            "Er is weinig speling en het rekenen kan enkele minuten duren.",
        )]
    return []


def _gz(p: Params) -> list[Signaal]:
    gz_only_vraag = sum(r for dl, r in p.req.items() if p.gz_only.get(dl, 0) == 1)
    gz_aanbod = sum(p.avail[e] for e in p.E if p.gz.get(e, 0) == 1)
    if gz_only_vraag > gz_aanbod:
        return [Signaal(
            "fout",
            f"Afdelingen die alleen door een gz-psycholoog gedaan mogen worden, vragen {_uren(gz_only_vraag)} uur, "
            f"maar de gz-psychologen hebben samen maar {_uren(gz_aanbod)} uur.",
        )]
    return []


def _mdo(p: Params) -> list[Signaal]:
    """Per afdeling met IDO-dag(en): zijn er genoeg medewerkers die op die dag(en) werken?"""
    details_fout, details_krap = [], []
    for (d, l), mdo in sorted(p.mdo_dag.items()):
        if (d, l) not in p.req or p.req[d, l] <= 0:
            continue
        mag = [e for e in p.E if p.avail[e] > 0 and (p.gz_only.get((d, l), 0) == 0 or p.gz.get(e, 0) == 1)]
        werkt_op = lambda e, dag: not p.werkdagen.get(e) or dag in p.werkdagen[e]  # noqa: E731
        kandidaten = [e for e in mag if any(werkt_op(e, dag) for dag in mdo)]
        uren = sum(p.avail[e] for e in kandidaten)
        naam = f"{d} ({l}), IDO op {' en '.join(DAG_NAMEN.get(dag, dag) for dag in mdo)}"
        leeg = [dag for dag in mdo if not any(werkt_op(e, dag) for e in mag)]
        if leeg:
            details_fout.append(f"{naam}: niemand die hier mag werken, werkt op {' en '.join(DAG_NAMEN.get(x, x) for x in leeg)}.")
        elif not kandidaten:
            details_fout.append(f"{naam}: niemand die hier mag werken, werkt op die dag.")
        elif uren < p.req[d, l]:
            details_krap.append(
                f"{naam}: wie op die dag werkt, heeft samen {_uren(uren)} uur; nodig is {_uren(p.req[d, l])}."
            )
        elif len(kandidaten) <= 2:
            details_krap.append(
                f"{naam}: alleen {' en '.join(kandidaten)} {'kan' if len(kandidaten) == 1 else 'kunnen'} hier werken."
            )
    result = []
    if details_fout:
        result.append(Signaal("fout", "Bij sommige afdelingen kan niemand (genoeg) op de IDO-dag aanwezig zijn.", details_fout))
    if details_krap:
        result.append(Signaal("waarschuwing", "Bij sommige afdelingen kunnen maar heel weinig mensen op de IDO-dag.", details_krap))
    zonder = [f"{d} ({l})" for (d, l) in p.req if (d, l) not in p.mdo_dag]
    if zonder and len(zonder) == len(p.req):
        result.append(Signaal("info", "Er zijn nog geen IDO's ingevuld (tabblad Afdelingen)."))
    elif zonder:
        result.append(Signaal("info", f"{len(zonder)} afdelingen hebben nog geen IDO.", zonder))
    return result


def _afspraken(p: Params) -> list[Signaal]:
    details = []
    dl_pairs = list(p.req)
    totaal_per_e: dict[str, float] = {}
    vrij = {
        (e, dl) for (e, d, groep, l, regel, _u) in p.afspraken if regel == "geen_ido"
        for dl in _min_uren_target_dl(p, dl_pairs, d, groep, l)
    }
    moet = {}  # e -> afdelingen met één IDO waar e moet werken en bij de IDO moet zijn
    for (e, d, groep, l, regel, uren) in p.afspraken:
        naam = d or groep or "alle afdelingen"
        doel = _min_uren_target_dl(p, dl_pairs, d, groep, l)
        if not doel:
            details.append(f"{e} – {naam}{f' ({l})' if l else ''}: deze afdeling bestaat niet op die locatie.")
        if regel in ("uitsluiten", "geen_ido"):
            continue
        if len(doel) == 1 and (e, doel[0]) not in vrij and len(p.ido.get(doel[0], ())) == 1:
            moet.setdefault(e, []).append(doel[0])
        minimum = uren if uren is not None else MINIMUM_AANWEZIGHEID
        totaal_per_e[e] = totaal_per_e.get(e, 0) + minimum
        dagen = p.werkdagen.get(e)
        if dagen and d is not None:
            mdo = {dag for dl in doel if (e, dl) not in vrij for dag in p.mdo_dag.get(dl, ())}
            if mdo and not (mdo & dagen):
                details.append(
                    f"{e} – {naam}: moet hier werken, maar werkt niet op de IDO-dag. Is dat goed, voeg dan een "
                    "afspraak 'Hoeft niet bij IDO' toe."
                )
    for e, plekken in moet.items():
        for i, a in enumerate(plekken):
            for b in plekken[i + 1:]:
                if ido_overlapt(p.ido[a][0], p.ido[b][0]):
                    details.append(
                        f"{e} moet op {a[0]} én {b[0]} werken, maar die IDO's zijn op hetzelfde moment "
                        f"({ido_tekst(p.ido[a][0])}). Is het één gezamenlijke IDO, geef ze dan hetzelfde label bij 'Samen'."
                    )
    for e, totaal in totaal_per_e.items():
        if totaal > p.avail.get(e, 0) + 1e-9:
            details.append(f"{e}: minimaal {_uren(totaal)} uur afgesproken, maar maar {_uren(p.avail.get(e, 0))} uur inzetbaar.")
    if details:
        return [Signaal("fout", "Sommige afspraken (Extra eisen) zijn niet haalbaar.", details)]
    return []


def _huidige_planning(p: Params) -> list[Signaal]:
    result = []
    per_e: dict[str, float] = {}
    per_dl: dict[tuple[str, str], float] = {}
    for (e, d, l), u in p.cur.items():
        per_e[e] = per_e.get(e, 0) + u
        per_dl[d, l] = per_dl.get((d, l), 0) + u
    te_veel = [
        f"{e}: {_uren(per_e[e])} uur ingepland, {_uren(p.avail[e])} inzetbaar"
        for e in p.E if per_e.get(e, 0) > p.avail[e] + 1e-9
    ]
    if te_veel:
        result.append(Signaal(
            "info",
            f"{len(te_veel)} medewerkers staan in de huidige planning voor meer uren dan ze inzetbaar zijn. "
            "De nieuwe planning past dat aan (telt als wijziging).",
            te_veel,
        ))
    leeg = [f"{d} ({l})" for (d, l) in p.req if per_dl.get((d, l), 0) == 0]
    if leeg:
        result.append(Signaal(
            "info",
            f"{len(leeg)} afdelingen hebben nog niemand in de huidige planning; die worden nieuw ingevuld.",
            leeg,
        ))
    return result


def _werkdagen(p: Params) -> list[Signaal]:
    zonder = [e for e in p.E if p.avail[e] > 0 and not p.werkdagen.get(e)]
    if zonder and p.mdo_dag:
        return [Signaal(
            "info",
            "Van deze medewerkers zijn geen werkdagen bekend; zij worden niet beperkt door de IDO-dagen.",
            zonder,
        )]
    return []


def _altijd_samen(p: Params) -> list[Signaal]:
    """Wie nu op locatie a werkt zonder b, krijgt sowieso een ander rooster.
    En wie op a moet werken maar van b is uitgesloten, kan niet worden ingepland."""
    result: list[Signaal] = []
    for a, b in p.altijd_samen:
        locs: dict[str, set[str]] = {}
        for (e, _d, l), uren in p.cur.items():
            if uren > 0:
                locs.setdefault(e, set()).add(l)
        nu = sorted(e for e, s in locs.items() if a in s and b not in s)
        if nu:
            result.append(Signaal(
                "info",
                f"{len(nu)} medewerker(s) werken nu op {a} maar niet op {b}. Wie op {a} werkt, moet ook op {b} "
                "werken, dus hun rooster verandert; dat telt mee als wijziging.",
                nu,
            ))
        moet_a = {e for (e, _d, _g, l, regel, _u) in p.afspraken if regel in ("minimaal", "precies") and l == a}
        niet_b = {e for (e, d, g, l, regel, _u) in p.afspraken
                  if regel == "uitsluiten" and l == b and d is None and g is None}
        botsing = sorted(moet_a & niet_b)
        if botsing:
            result.append(Signaal(
                "fout",
                f"Een afspraak (Extra eisen) zet iemand op {a} en sluit die persoon uit van {b}, terwijl wie op {a} "
                f"werkt ook op {b} moet werken.",
                botsing,
            ))
    return result


def _nooit_samen(p: Params) -> list[Signaal]:
    """Wie nu op twee locaties werkt die 'nooit' gecombineerd mogen worden,
    krijgt sowieso een ander rooster. En een afspraak 'minimaal'/'precies' op
    allebei die locaties maakt een planning onmogelijk."""
    result: list[Signaal] = []
    for a, b in p.nooit_samen:
        locs: dict[str, set[str]] = {}
        for (e, _d, l), uren in p.cur.items():
            if uren > 0:
                locs.setdefault(e, set()).add(l)
        nu = sorted(e for e, s in locs.items() if {a, b} <= s)
        if nu:
            result.append(Signaal(
                "info",
                f"{len(nu)} medewerker(s) werken nu op {a} én {b}. Dat mag niet meer, dus hun rooster verandert; "
                "dat telt mee als wijziging.",
                nu,
            ))
        verplicht: dict[str, set[str]] = {}
        for (e, _d, _g, l, regel, _u) in p.afspraken:
            if regel in ("minimaal", "precies") and l is not None:
                verplicht.setdefault(e, set()).add(l)
        botsing = sorted(e for e, s in verplicht.items() if {a, b} <= s)
        if botsing:
            result.append(Signaal(
                "fout",
                f"Een afspraak (Extra eisen) dwingt iemand op {a} én {b}, terwijl die combinatie nooit mag.",
                botsing,
            ))
    return result
