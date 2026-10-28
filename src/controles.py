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

from data import DAG_NAMEN, Params, params_from_tables, valideer
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
    result += _minimum_uren(p)
    result += _huidige_planning(p)
    result += _werkdagen(p)
    volgorde = {"fout": 0, "waarschuwing": 1, "info": 2}
    return sorted(result, key=lambda s: volgorde[s.niveau])


def _capaciteit(p: Params) -> list[Signaal]:
    vereist, inzetbaar = sum(p.req.values()), sum(p.avail.values())
    ruimte = inzetbaar - vereist
    if ruimte < 0:
        return [Signaal(
            "fout",
            f"Er is {_uren(-ruimte)} uur per week te weinig: {_uren(inzetbaar)} uur inzetbaar, {_uren(vereist)} uur vereist. "
            "Er is geen planning mogelijk.",
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
    """Per afdeling met een MDO-dag: zijn er genoeg medewerkers die op die dag werken?"""
    details_fout, details_krap = [], []
    for (d, l), dag in sorted(p.mdo_dag.items()):
        if (d, l) not in p.req:
            continue
        kandidaten = [
            e for e in p.E
            if p.avail[e] > 0
            and (not p.werkdagen.get(e) or dag in p.werkdagen[e])
            and (p.gz_only.get((d, l), 0) == 0 or p.gz.get(e, 0) == 1)
        ]
        uren = sum(p.avail[e] for e in kandidaten)
        naam = f"{d} ({l}), MDO op {DAG_NAMEN[dag]}"
        if not kandidaten:
            details_fout.append(f"{naam}: niemand die hier mag werken, werkt op die dag.")
        elif uren < p.req[d, l]:
            details_fout.append(
                f"{naam}: wie op die dag werkt, heeft samen {_uren(uren)} uur; nodig is {_uren(p.req[d, l])}."
            )
        elif len(kandidaten) <= 2:
            details_krap.append(
                f"{naam}: alleen {' en '.join(kandidaten)} {'kan' if len(kandidaten) == 1 else 'kunnen'} hier werken."
            )
    result = []
    if details_fout:
        result.append(Signaal("fout", "Bij sommige afdelingen kan niemand (genoeg) op de MDO-dag aanwezig zijn.", details_fout))
    if details_krap:
        result.append(Signaal("waarschuwing", "Bij sommige afdelingen kunnen maar heel weinig mensen op de MDO-dag.", details_krap))
    zonder = [f"{d} ({l})" for (d, l) in p.req if (d, l) not in p.mdo_dag]
    if zonder and len(zonder) == len(p.req):
        result.append(Signaal("info", "Er zijn nog geen MDO-dagen ingevuld (tabblad Afdelingen)."))
    elif zonder:
        result.append(Signaal("info", f"{len(zonder)} afdelingen hebben nog geen MDO-dag.", zonder))
    return result


def _minimum_uren(p: Params) -> list[Signaal]:
    details = []
    dl_pairs = list(p.req)
    totaal_per_e: dict[str, float] = {}
    for (e, d, groep, l, uren) in p.min_uren:
        minimum = uren if uren is not None else MINIMUM_AANWEZIGHEID
        totaal_per_e[e] = totaal_per_e.get(e, 0) + minimum
        naam = d or groep
        if not _min_uren_target_dl(p, dl_pairs, d, groep, l):
            details.append(f"{e} – {naam}{f' ({l})' if l else ''}: deze afdeling bestaat niet op die locatie.")
        dagen = p.werkdagen.get(e)
        if dagen and d is not None:
            mdo = {p.mdo_dag.get(dl) for dl in _min_uren_target_dl(p, dl_pairs, d, groep, l)} - {None}
            if mdo and not (mdo & dagen):
                details.append(f"{e} – {naam}: moet hier werken, maar werkt niet op de MDO-dag.")
    for e, totaal in totaal_per_e.items():
        if totaal > p.avail.get(e, 0) + 1e-9:
            details.append(f"{e}: minimaal {_uren(totaal)} uur verplicht, maar maar {_uren(p.avail.get(e, 0))} uur inzetbaar.")
    if details:
        return [Signaal("fout", "Sommige minimumuren (Extra eisen) zijn niet haalbaar.", details)]
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
            "Van deze medewerkers zijn geen werkdagen bekend; zij worden niet beperkt door de MDO-dagen.",
            zonder,
        )]
    return []
