"""Pagina 'Planning': rooster berekenen (op de achtergrond, met voortgang),
bekijken, vergelijken met de huidige planning, downloaden, bewaren in het
archief en overnemen als huidige planning.

toon_kerncijfers() en toon_rooster() worden ook door de archiefpagina gebruikt;
voortgang_zijbalk() door app.py.
"""

import hashlib
from datetime import date

import pandas as pd
import streamlit as st

import achtergrond
import store
from controles import signalen
from data import DAG_NAMEN, WERKDAGEN, Params, params_from_tables, valideer
from model import CONSTRAINT_GROEPEN
from output import excel_bytes
from solve import STAPPEN, huidig_rooster, vergelijk_roosters
from ui.helpers import STATUS_LABELS, kleur_status, legenda, nl_datum, nl_getal, toon_signalen
from ui.stijl import paginakop

UITGEZONDERD_HEADCOUNT = "AGT"  # telt niet mee in doel 3 (zie solve.py)


# ------------------------------------------------------------------ rekenen op de achtergrond


def invoer_hash(tabellen: dict[str, pd.DataFrame]) -> str:
    """Vingerafdruk van de invoer, om te zien of een resultaat nog actueel is."""
    h = hashlib.sha256()
    for naam in sorted(tabellen):
        if naam == "Locaties":
            continue  # alleen een keuzelijst; telt niet mee voor de planning
        h.update(naam.encode())
        h.update(tabellen[naam].to_json(orient="split").encode())
    return h.hexdigest()


def start_planning() -> bool:
    """Start de berekening op de achtergrond met de opgeslagen invoer."""
    tabellen = store.lees_tabellen()
    if valideer(params_from_tables(tabellen)):
        st.error("De invoer bevat fouten; los die eerst op (zie pagina Invoer).")
        return False
    if not achtergrond.start(tabellen, invoer_hash(tabellen)):
        st.warning("Er wordt al een planning berekend.")
        return False
    st.session_state["volg_planning"] = True
    return True


def _tijd(seconden: float) -> str:
    m, s = divmod(int(seconden), 60)
    return f"{m}:{s:02d}"


def _toon_balk(taak, compact: bool = False) -> None:
    stap = max(taak.stap, 1)
    tekst = f"Stap {stap} van {len(STAPPEN)}: {taak.omschrijving or STAPPEN[0]}"
    if compact:
        st.progress(taak.fractie(), text=f"Planning: stap {stap}/{len(STAPPEN)} · {_tijd(taak.verstreken())}")
    else:
        st.progress(taak.fractie(), text=f"{tekst}  ·  {_tijd(taak.verstreken())} verstreken")


@st.fragment(run_every=1.5)
def _voortgang_hoofd() -> None:
    """Voortgangsbalk op de planningspagina; ververst zichzelf. Bij afloop
    wordt de hele pagina opnieuw geladen om het resultaat te tonen."""
    taak = achtergrond.status()
    if taak is None:
        return
    if taak.loopt:
        _toon_balk(taak)
        st.caption(
            "Je kunt gerust naar een andere pagina gaan; de berekening loopt door. "
            "Wijzigingen in de invoer kunnen pas worden opgeslagen als de berekening klaar is."
        )
        st.session_state["volg_planning"] = True
    elif st.session_state.pop("volg_planning", False):
        st.rerun(scope="app")


@st.fragment(run_every=2)
def voortgang_zijbalk() -> None:
    """Klein voortgangsbalkje in de zijbalk, zichtbaar op elke pagina."""
    taak = achtergrond.status()
    if taak is None:
        return
    if taak.loopt:
        _toon_balk(taak, compact=True)
        st.session_state["zijbalk_bezig"] = True
    elif st.session_state.pop("zijbalk_bezig", False):
        st.rerun(scope="app")  # pagina's verversen (bijv. Opslaan-knoppen weer aan)
    elif taak.geslaagd:
        st.success("Nieuwe planning klaar", icon=":material/check_circle:")
    elif taak.onoplosbaar is not None or taak.fout:
        st.error("Plannen is niet gelukt", icon=":material/error:")


def _toon_afloop() -> None:
    """Melding over de laatst afgeronde berekening."""
    taak = achtergrond.status()
    if taak is None or taak.loopt:
        return
    if taak.fout:
        st.error(f"Plannen is mislukt: {taak.fout}", icon=":material/error:")
    elif taak.onoplosbaar is not None:
        tekst = "**Er is geen planning mogelijk die aan alle regels voldoet.**\n\n"
        if taak.onoplosbaar:
            tekst += "De planning wordt wél mogelijk als je één van deze groepen regels versoepelt. Daar zit dus het knelpunt:\n"
            tekst += "\n".join(f"- {CONSTRAINT_GROEPEN[g]}" for g in taak.onoplosbaar)
        else:
            tekst += (
                "Het knelpunt zit in een combinatie van meerdere groepen regels. Controleer vooral of de inzetbare tijd "
                "de vereiste uren dekt, en of de minimumuren, MDO-dagen en vastzettingen niet te krap zijn."
            )
        st.error(tekst, icon=":material/error:")
    elif taak.geslaagd:
        st.success(f"Nieuwe planning berekend in {_tijd(taak.verstreken())} minuten.", icon=":material/check_circle:")


# ------------------------------------------------------------------ kerncijfers


def _uren(x) -> str:
    return f"{x:g}".replace(".", ",")


def _koppels_per_afdeling(rooster: pd.DataFrame) -> pd.Series:
    r = rooster[(rooster["uren"] > 0) & (rooster["afdeling"] != UITGEZONDERD_HEADCOUNT)]
    return r.groupby("afdeling")["medewerker"].apply(lambda s: sorted(set(s)))


def _koppels(rooster: pd.DataFrame) -> int:
    return int(_koppels_per_afdeling(rooster).map(len).sum())


def _locaties_per_medewerker(rooster: pd.DataFrame) -> pd.Series:
    return rooster[rooster["uren"] > 0].groupby("medewerker")["locatie"].apply(set)


def _reizigers(rooster: pd.DataFrame, l1: str, l2: str) -> list[str]:
    return sorted(e for e, s in _locaties_per_medewerker(rooster).items() if l1 in s and l2 in s)


@st.dialog("Gewijzigde toewijzingen", width="large")
def _dialoog_wijzigingen(cmp: pd.DataFrame, label_oud: str, label_nieuw: str) -> None:
    anders = cmp[cmp["status"] != "gelijk"]
    st.markdown(
        f"Een **toewijzing** is één medewerker op één afdeling op één locatie. Hieronder staan de "
        f"**{len(anders)}** toewijzingen die anders zijn dan {label_oud.lower()}."
    )
    legenda()
    tabel = anders.rename(columns={"medewerker": "Medewerker", "afdeling": "Afdeling", "locatie": "Locatie",
                                   "uren_nu": label_oud, "uren_nieuw": label_nieuw})
    tabel = tabel[["Medewerker", "Afdeling", "Locatie", label_oud, label_nieuw, "Status"]]
    st.dataframe(kleur_status(tabel).format(nl_getal, subset=[label_oud, label_nieuw]), hide_index=True, width="stretch")


@st.dialog("Reizen tussen locaties", width="large")
def _dialoog_reizen(p: Params, rooster: pd.DataFrame, oud: pd.DataFrame, label_oud: str) -> None:
    nieuw_r, oud_r = _reizigers(rooster, p.l1, p.l2), _reizigers(oud, p.l1, p.l2)
    st.markdown(
        f"Medewerkers die zowel op **{p.l1}** als op **{p.l2}** werken, moeten tussen die locaties reizen. "
        f"In deze planning zijn dat er **{len(nieuw_r)}**, {label_oud.lower()} **{len(oud_r)}**."
    )
    rijen = []
    for e in sorted(set(nieuw_r) | set(oud_r)):
        r = rooster[(rooster["medewerker"] == e) & (rooster["uren"] > 0)]
        rijen.append({
            "Medewerker": e,
            f"Reist {label_oud.lower()}": "ja" if e in oud_r else "nee",
            "Reist in deze planning": "ja" if e in nieuw_r else "nee",
            f"Uren op {p.l1}": r.loc[r["locatie"] == p.l1, "uren"].sum(),
            f"Uren op {p.l2}": r.loc[r["locatie"] == p.l2, "uren"].sum(),
            "Afdelingen": ", ".join(f"{a} ({l})" for a, l in zip(r["afdeling"], r["locatie"])),
        })
    if rijen:
        st.dataframe(pd.DataFrame(rijen), hide_index=True, width="stretch")
    else:
        st.success("Niemand hoeft te reizen.")


@st.dialog("Medewerker-afdeling-koppels", width="large")
def _dialoog_koppels(rooster: pd.DataFrame, oud: pd.DataFrame, label_oud: str) -> None:
    nieuw_k, oud_k = _koppels_per_afdeling(rooster), _koppels_per_afdeling(oud)
    st.markdown(
        f"Per afdeling: hoeveel verschillende medewerkers er werken ({UITGEZONDERD_HEADCOUNT} telt niet mee). "
        f"Opgeteld is dat **{_koppels(rooster)}** in deze planning en **{_koppels(oud)}** {label_oud.lower()}. "
        "Minder is beter: dan heeft een afdeling vaste gezichten."
    )
    rijen = []
    for d in sorted(set(nieuw_k.index) | set(oud_k.index)):
        n, o = nieuw_k.get(d, []), oud_k.get(d, [])
        rijen.append({"Afdeling": d, label_oud: len(o), "Deze planning": len(n), "Verschil": len(n) - len(o),
                      "Wie (deze planning)": ", ".join(n)})
    df = pd.DataFrame(rijen).sort_values(["Verschil", "Afdeling"])
    st.dataframe(
        df.style.map(lambda v: "color: #1e8449; font-weight: 600" if v < 0 else ("color: #c0392b; font-weight: 600" if v > 0 else ""),
                     subset=["Verschil"]),
        hide_index=True, width="stretch",
    )


@st.dialog("Ingeplande uren", width="large")
def _dialoog_uren(p: Params, rooster: pd.DataFrame) -> None:
    st.markdown(
        "Totaal ingeplande uren per week, vergeleken met wat de afdelingen nodig hebben en wat de medewerkers "
        "inzetbaar zijn. Iets meer dan vereist is normaal, bijvoorbeeld als iemands huidige uren op een afdeling "
        "gelijk blijven om wijzigingen te voorkomen."
    )
    links, rechts = st.columns(2, gap="large")
    with links:
        st.markdown("**Per locatie**")
        per_loc = pd.DataFrame([
            {"Locatie": l,
             "Vereiste uren": sum(r for (d, ll), r in p.req.items() if ll == l),
             "Geplande uren": rooster.loc[rooster["locatie"] == l, "uren"].sum()}
            for l in p.L
        ])
        per_loc["Verschil"] = per_loc["Geplande uren"] - per_loc["Vereiste uren"]
        st.dataframe(per_loc.style.format(nl_getal, subset=["Vereiste uren", "Geplande uren", "Verschil"]), hide_index=True, width="stretch")
    with rechts:
        st.markdown("**Per medewerker**")
        per_e = pd.DataFrame([
            {"Medewerker": e, "Inzetbaar": p.avail[e], "Gepland": rooster.loc[rooster["medewerker"] == e, "uren"].sum()}
            for e in p.E
        ])
        per_e["Niet ingepland"] = per_e["Inzetbaar"] - per_e["Gepland"]
        st.dataframe(per_e.style.format(nl_getal, subset=["Inzetbaar", "Gepland", "Niet ingepland"]), hide_index=True, width="stretch")


def toon_kerncijfers(p: Params, rooster: pd.DataFrame, oud: pd.DataFrame, sleutel: str, label_oud: str = "Nu") -> None:
    """De drie doelen van het model + totaal ingeplande uren, vergeleken met `oud`.
    Onder elk cijfer opent de knop Details een venster met wat erachter zit."""
    cmp = vergelijk_roosters(oud, rooster)
    cmp["Status"] = cmp["status"].map(STATUS_LABELS)
    wijzigingen = int((cmp["status"] != "gelijk").sum())
    reis_nieuw, reis_oud = len(_reizigers(rooster, p.l1, p.l2)), len(_reizigers(oud, p.l1, p.l2))
    k_nieuw, k_oud = _koppels(rooster), _koppels(oud)
    ingepland, vereist = rooster["uren"].sum(), sum(p.req.values())
    vgl = label_oud.lower()

    kolommen = st.columns(4)
    with kolommen[0].container(border=True, key=f"kaart_{sleutel}_1"):
        st.metric(
            "Gewijzigde toewijzingen", wijzigingen,
            delta=f"van de {len(cmp)} toewijzingen", delta_color="off", delta_arrow="off",
            help=f"Hoeveel toewijzingen anders zijn dan {vgl}. Een toewijzing is één medewerker op één afdeling op één "
            "locatie; nieuw, gestopt of een ander aantal uren telt elk als 1. Dit is het eerste en belangrijkste doel: "
            "zo weinig mogelijk wijzigingen.",
        )
        if st.button("Details", icon=":material/open_in_full:", type="tertiary", key=f"{sleutel}_d1"):
            _dialoog_wijzigingen(cmp, label_oud, "Deze planning")
    with kolommen[1].container(border=True, key=f"kaart_{sleutel}_2"):
        st.metric(
            f"Werkt op {p.l1} én {p.l2}", reis_nieuw, delta=reis_nieuw - reis_oud, delta_color="inverse",
            help=f"Aantal medewerkers dat zowel op {p.l1} als op {p.l2} werkt en dus moet reizen. Tweede doel: zo weinig "
            f"mogelijk. Het getal met het pijltje is het verschil met {vgl} (groen = minder reizen).",
        )
        if st.button("Details", icon=":material/open_in_full:", type="tertiary", key=f"{sleutel}_d2"):
            _dialoog_reizen(p, rooster, oud, label_oud)
    with kolommen[2].container(border=True, key=f"kaart_{sleutel}_3"):
        st.metric(
            "Medewerker-afdeling-koppels", k_nieuw, delta=k_nieuw - k_oud, delta_color="inverse",
            help=f"Per afdeling het aantal verschillende medewerkers, opgeteld ({UITGEZONDERD_HEADCOUNT} telt niet mee). "
            "Werkt iemand op 3 afdelingen, dan telt die 3 keer. Derde doel: liever weinig verschillende mensen per "
            f"afdeling. Het pijltje is het verschil met {vgl} (groen = minder).",
        )
        if st.button("Details", icon=":material/open_in_full:", type="tertiary", key=f"{sleutel}_d3"):
            _dialoog_koppels(rooster, oud, label_oud)
    with kolommen[3].container(border=True, key=f"kaart_{sleutel}_4"):
        st.metric(
            "Ingeplande uren per week", _uren(ingepland), delta=f"{_uren(ingepland - vereist)} t.o.v. vereist", delta_color="off",
            help=f"Totaal aantal ingeplande uren per week. De afdelingen hebben samen {_uren(vereist)} uur nodig; "
            "het getal eronder is het verschil daarmee.",
        )
        if st.button("Details", icon=":material/open_in_full:", type="tertiary", key=f"{sleutel}_d4"):
            _dialoog_uren(p, rooster)


# ------------------------------------------------------------------ rooster


def toon_rooster(
    p: Params, rooster: pd.DataFrame, oud: pd.DataFrame, sleutel: str,
    label_oud: str = "Nu", label_nieuw: str = "Nieuw",
) -> dict[str, pd.DataFrame]:
    """Toont een rooster in vier tabbladen, vergeleken met `oud`.
    Geeft de getoonde tabellen terug (voor de Excel-download)."""
    cmp = vergelijk_roosters(oud, rooster)
    cmp["Status"] = cmp["status"].map(STATUS_LABELS)

    n_wijz = int((cmp["status"] != "gelijk").sum())
    tab_loc, tab_med, tab_wijz, tab_afd = st.tabs(
        [":material/location_on: Per locatie", ":material/person: Per medewerker", f":material/sync_alt: Wijzigingen ({n_wijz})", ":material/apartment: Per afdeling"]
    )

    # --- per locatie
    with tab_loc:
        locaties = sorted(set(cmp["locatie"]) | set(l for _d, l in p.req))
        kolom1, kolom2 = st.columns([3, 1])
        gekozen = kolom1.segmented_control(
            "Locatie", locaties, default=locaties[0] if locaties else None, key=f"{sleutel}_loc", label_visibility="collapsed"
        ) or (locaties[0] if locaties else None)
        alles = kolom2.toggle("Ook ongewijzigd tonen", value=True, key=f"{sleutel}_alles")
        if gekozen:
            sel = cmp[cmp["locatie"] == gekozen]
            vereist = sum(r for (d, l), r in p.req.items() if l == gekozen)
            m1, m2, m3 = st.columns(3)
            m1.metric("Vereiste uren per week", _uren(vereist), help=f"Wat de afdelingen op {gekozen} samen nodig hebben.")
            m2.metric("Geplande uren per week", _uren(sel["uren_nieuw"].sum()), help=f"Wat er in deze planning op {gekozen} is ingepland.")
            m3.metric("Aantal medewerkers", sel.loc[sel["uren_nieuw"] > 0, "medewerker"].nunique(), help=f"Verschillende medewerkers die op {gekozen} werken.")
            if not alles:
                sel = sel[sel["status"] != "gelijk"]
            tabel = sel.sort_values(["afdeling", "medewerker"]).rename(
                columns={"afdeling": "Afdeling", "medewerker": "Medewerker", "uren_nu": label_oud, "uren_nieuw": label_nieuw}
            )[["Afdeling", "Medewerker", label_oud, label_nieuw, "Status"]]
            legenda()
            st.dataframe(
                kleur_status(tabel).format(nl_getal, subset=[label_oud, label_nieuw]),
                hide_index=True, width="stretch", height="content",
            )

    # --- per medewerker
    with tab_med:
        nieuw = rooster[rooster["uren"] > 0]
        oud_pos = oud[oud["uren"] > 0]
        rijen = []
        for e in sorted(set(p.E) | set(nieuw["medewerker"])):
            r = nieuw[nieuw["medewerker"] == e].sort_values(["locatie", "afdeling"])
            rijen.append({
                "Medewerker": e,
                "Werkdagen": " ".join(DAG_NAMEN[d][:2] for d in WERKDAGEN if d in p.werkdagen.get(e, set())),
                "Inzetbaar": p.avail.get(e, 0.0),
                "Ingepland": r["uren"].sum(),
                f"Afdelingen {label_oud.lower()}": oud_pos.loc[oud_pos["medewerker"] == e, "afdeling"].nunique(),
                f"Afdelingen {label_nieuw.lower()}": r["afdeling"].nunique(),
                "Locaties": ", ".join(sorted(set(r["locatie"]))),
                "Rooster": "  ·  ".join(f"{a} ({l}) {_uren(u)}" for a, l, u in zip(r["afdeling"], r["locatie"], r["uren"])),
            })
        per_med = pd.DataFrame(rijen)
        st.dataframe(
            per_med.style.format(nl_getal, subset=["Inzetbaar", "Ingepland"]),
            hide_index=True, width="stretch", height="content",
            column_config={"Rooster": st.column_config.TextColumn(width="large")},
        )

    # --- wijzigingen
    with tab_wijz:
        anders = cmp[cmp["status"] != "gelijk"].rename(
            columns={"medewerker": "Medewerker", "afdeling": "Afdeling", "locatie": "Locatie",
                     "uren_nu": label_oud, "uren_nieuw": label_nieuw}
        )[["Medewerker", "Afdeling", "Locatie", label_oud, label_nieuw, "Status"]]
        if anders.empty:
            st.success(f"Geen verschillen met {label_oud.lower()}.")
        else:
            legenda()
            st.dataframe(
                kleur_status(anders).format(nl_getal, subset=[label_oud, label_nieuw]),
                hide_index=True, width="stretch", height="content",
            )

    # --- per afdeling
    with tab_afd:
        rijen = []
        for (d, l), r in sorted(p.req.items()):
            sel = cmp[(cmp["afdeling"] == d) & (cmp["locatie"] == l)]
            rijen.append({
                "Afdeling": d, "Locatie": l, "Vereiste uren": r, "Geplande uren": sel["uren_nieuw"].sum(),
                f"Medewerkers {label_oud.lower()}": int((sel["uren_nu"] > 0).sum()),
                f"Medewerkers {label_nieuw.lower()}": int((sel["uren_nieuw"] > 0).sum()),
                "Wie": ", ".join(sorted(sel.loc[sel["uren_nieuw"] > 0, "medewerker"])),
            })
        per_afd = pd.DataFrame(rijen)
        st.dataframe(
            per_afd.style.format(nl_getal, subset=["Vereiste uren", "Geplande uren"]),
            hide_index=True, width="stretch", height="content",
        )

    return {
        "Rooster": rooster[["locatie", "afdeling", "medewerker", "uren"]].sort_values(["locatie", "afdeling", "medewerker"]),
        "Wijzigingen": anders,
        "Per medewerker": per_med,
        "Per afdeling": per_afd,
    }


# ------------------------------------------------------------------ pagina


def pagina_planning() -> None:
    paginakop("Planning", "Bereken het rooster, bekijk wat er verandert ten opzichte van nu en bewaar het resultaat.")
    if not store.heeft_gegevens():
        st.info("Er is nog geen invoer. Ga naar **Invoer** om het team en de afdelingen in te voeren.")
        return

    tabellen = store.lees_tabellen()
    p = params_from_tables(tabellen)
    lijst = signalen(tabellen)
    blokkerend = any(s.niveau == "fout" for s in lijst)
    bezig = achtergrond.bezig()
    toon_signalen(lijst, alleen_belangrijk=True)

    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    if c1.button("Plan maken", icon=":material/play_arrow:", type="primary", disabled=blokkerend or bezig, width="stretch"):
        if start_planning():
            st.rerun()
    c2.caption(
        "Berekent het beste rooster op basis van de opgeslagen invoer: eerst zo min mogelijk wijzigingen, "
        f"dan zo min mogelijk reizen tussen {p.l1} en {p.l2}, dan zo min mogelijk mensen per afdeling. "
        "Dit kan enkele minuten duren."
    )
    _voortgang_hoofd()
    _toon_afloop()

    opgeslagen = store.lees_resultaat()
    if opgeslagen is None:
        if not bezig:
            st.caption("Nog geen planning berekend.")
        return
    rooster, meta = opgeslagen
    kpi = meta["kpi"]

    st.divider()
    st.subheader("Resultaat")
    st.caption(f"Berekend op {nl_datum(meta['tijdstip'])}")
    if kpi.get("invoer") != invoer_hash(tabellen):
        st.warning("De invoer of bijsturing is gewijzigd sinds deze planning is berekend. Klik op **Plan maken** voor een actuele planning.")
    if kpi.get("niet_bewezen_optimaal"):
        st.info(
            "De rekentijd was op voordat bewezen kon worden dat dit de allerbeste planning is "
            f"(stap: {', '.join(kpi['niet_bewezen_optimaal'])}). Het rooster voldoet wel aan alle regels; "
            "mogelijk bestaat er een planning met nog iets minder wijzigingen of reizen."
        )

    oud = huidig_rooster(p)
    toon_kerncijfers(p, rooster, oud, sleutel="plan")
    tabellen_export = toon_rooster(p, rooster, oud, sleutel="plan")

    st.divider()
    a1, a2, a3 = st.columns(3)
    a1.download_button(
        "Download als Excel", icon=":material/download:", data=excel_bytes(tabellen_export), file_name=f"planning_{date.today():%Y-%m-%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch",
    )
    with a2.popover("Bewaren in archief", icon=":material/inventory_2:", width="stretch"):
        naam = st.text_input("Naam", value=f"Planning {date.today():%d-%m-%Y}", help="Bijvoorbeeld de periode: 'Q1 2027'.")
        if st.button("Bewaren", type="primary", disabled=not naam.strip()):
            store.archiveer(naam.strip(), rooster, kpi)
            st.toast(f"'{naam.strip()}' bewaard in het archief.", icon=":material/check_circle:")
    with a3.popover("Overnemen als huidige planning", icon=":material/published_with_changes:", width="stretch", disabled=bezig):
        st.markdown(
            "Hiermee wordt dit rooster de nieuwe **huidige planning**. De volgende planning vergelijkt dan met dit "
            "rooster. Tip: bewaar de oude situatie eerst in het archief."
        )
        if st.button("Ja, overnemen", type="primary"):
            nieuw = rooster.rename(columns={"uren": "huidige_uren"})[["medewerker", "afdeling", "locatie", "huidige_uren"]]
            store.schrijf_tabel("CurrentSchedule", nieuw)
            st.toast("Huidige planning bijgewerkt.", icon=":material/check_circle:")
            st.rerun()
