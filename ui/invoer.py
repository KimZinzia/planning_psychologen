"""Pagina 'Invoer': team, afdelingen, huidige planning en extra eisen, met
aandachtspunten bovenaan (src/controles.py) en een back-up onderaan.

Namen van medewerkers, locaties en afdelingen voer je op één plek in; elders
kies je ze uit een keuzelijst. Hernoemen of verwijderen werkt door in de
andere tabellen (zie src/beheer.py).
"""

from datetime import date

import pandas as pd
import streamlit as st

import achtergrond
import store
from beheer import vul_locaties_aan, wijzig_medewerkers, wijzig_vereisten
from controles import signalen
from data import (
    AFTREK_KOLOMMEN,
    DAG_NAMEN,
    PIOG_PRODUCTIVITEIT,
    TABELLEN,
    WERKDAGEN,
    bereken_inzetbare_tijd,
    bereken_uren_opbouw,
    load_excel_tables,
)
from output import excel_bytes
from ui.stijl import paginakop
from ui.helpers import (
    VERWIJDER,
    na_editor,
    naar_bool,
    naar_int,
    namen,
    nl_getal,
    toon_signalen,
    uren_kolom,
    verwijder_kolom,
    voor_editor,
)

UREN_PER_FTE = 36  # één fulltime aanstelling, in uren per week
BEREKEND = ["Inzetbare tijd", "Netto", "PIOG"]  # kolommen die alleen getoond worden, niet opgeslagen

BOOL_EMPLOYEES =["gz_psycholoog", "In opleiding", "zwangerschapsverlof", *WERKDAGEN]
DAG_LABELS = {"ma": "Ma", "di": "Di", "wo": "Wo", "do": "Do", "vr": "Vr"}
AFTREK_LABELS = {
    "lesuren PIOG": "Lesuren PIOG",
    "Opleidingsuren PIOG": "Opleidingsuren PIOG",
    "extern opleiding": "Externe opleiding",
    "ouderschapsverlof": "Ouderschapsverlof",
    "overig": "Overig",
    "Opleidingstijd": "Opleidingstijd",
    "Coordinerende taken": "Coördinerende taken",
}

UITLEG_RIJEN = (
    ":material/add_circle: **Toevoegen:** klik op de lege rij onderaan de tabel.  \n"
    ":material/cancel: **Verwijderen:** vink de rij aan in de eerste kolom (❌).  \n"
    ":material/save: Klik daarna op **Opslaan**; pas dan worden de wijzigingen bewaard."
)


def _plek(afdeling, locatie) -> str:
    return f"{afdeling} ({locatie})"


def _opslaan(resultaat) -> None:
    """Slaat de tabellen op als er geen fouten zijn, en toont de meldingen na herladen."""
    tabellen, meldingen, fouten = resultaat
    if achtergrond.bezig():
        fouten = ["Er wordt een planning berekend; wijzigingen kun je opslaan als die klaar is."]
    if fouten:
        for f in fouten:
            st.error(f)
        return
    store.schrijf_tabellen(tabellen)
    st.session_state["meldingen"] = meldingen or ["Opgeslagen"]
    st.rerun()


def pagina_invoer() -> None:
    paginakop(
        "Invoer",
        "Hier staat alles wat de planning nodig heeft.<br>"
        "Werk het bij als het team of de afdelingen veranderen.<br>"
        "<strong>Nieuwe medewerkers voer je maar op één plek in</strong> (tabblad Team); "
        "in de andere tabellen kies je ze uit een lijst.",
    )
    for melding in st.session_state.pop("meldingen", []):
        st.toast(melding, icon=":material/check_circle:")
    if achtergrond.bezig():
        st.info(
            "Er wordt een planning berekend. Je kunt alles bekijken, maar wijzigingen opslaan kan pas als de "
            "berekening klaar is (de voortgang staat links in de zijbalk).",
            icon=":material/hourglass_top:",
        )

    tabellen = store.lees_tabellen()
    tabellen, aangevuld = vul_locaties_aan(tabellen)
    if aangevuld:
        store.schrijf_tabel("Locaties", tabellen["Locaties"])

    emp = tabellen["Employees"]
    inzetbaar = bereken_inzetbare_tijd(emp)
    fte = inzetbaar.sum() / UREN_PER_FTE
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Medewerkers", len(emp))
    c2.metric("Waarvan gz-psycholoog", int(pd.to_numeric(emp["gz_psycholoog"], errors="coerce").fillna(0).sum()))
    c3.metric("Inzetbare uren per week", f"{inzetbaar.sum():g}")
    c4.metric(
        "Inzetbare FTE",
        f"{fte:.2f}".replace(".", ","),
        help=f"Inzetbare uren per week gedeeld door {UREN_PER_FTE} uur per FTE.",
    )
    st.caption(
        f"Het beschikbaar aantal FTE is bruto FTE na aftrek van verlof, verzuim en scholing "
        f"(inzetbare uren ÷ {UREN_PER_FTE})."
    )
    toon_signalen(signalen(tabellen))

    tab_team, tab_afd, tab_huidig, tab_eisen = st.tabs(
        [":material/group: Team", ":material/apartment: Afdelingen", ":material/calendar_view_week: Huidige planning", ":material/rule: Extra eisen"]
    )
    with tab_team:
        _tab_team(tabellen, inzetbaar)
    with tab_afd:
        _tab_afdelingen(tabellen)
    with tab_huidig:
        _tab_huidige_planning(tabellen, inzetbaar)
    with tab_eisen:
        _tab_extra_eisen(tabellen)

    st.divider()
    _backup(tabellen)


# ------------------------------------------------------------------ Team


def _tab_team(tabellen, inzetbaar: pd.Series) -> None:
    emp = naar_bool(tabellen["Employees"], BOOL_EMPLOYEES)
    emp.insert(1, "Inzetbare tijd", inzetbaar)
    # Tussenstappen, alleen ter controle in de tabel Uren (niet opgeslagen).
    opbouw = bereken_uren_opbouw(tabellen["Employees"])
    emp["Netto"] = opbouw["netto"]
    emp["PIOG"] = opbouw["piog_correctie"].where(opbouw["piog_correctie"] != 0)  # leeg voor niet-PIOG
    emp = voor_editor(emp, ["medewerker"])

    with st.container(border=True):
        st.subheader("Medewerkers")
        st.caption(
            "Een nieuwe medewerker voeg je **alleen hier** toe. Hernoem je iemand, dan verandert de naam overal mee; "
            "verwijder je iemand, dan verdwijnen ook diens regels uit de andere tabellen.  \n"
            "🔒 Contracturen en inzetbare tijd pas je aan in de tabel **Uren** hieronder "
            "(bij een nieuwe medewerker: eerst hier opslaan, dan daar de uren invullen)."
        )
        with st.expander("Hoe voeg ik rijen toe of verwijder ik ze?"):
            st.markdown(UITLEG_RIJEN)
        with st.form("team_basis", border=False):
            bewerkt = st.data_editor(
                emp,
                key="editor_team",
                num_rows="add",
                hide_index=True,
                width="stretch",
                height="content",
                column_order=[VERWIJDER, "medewerker", "gz_psycholoog", "In opleiding", "Contracturen", "Inzetbare tijd", *WERKDAGEN],
                column_config={
                    VERWIJDER: verwijder_kolom(),
                    "medewerker": st.column_config.TextColumn("Naam", required=True, pinned=True),
                    "gz_psycholoog": st.column_config.CheckboxColumn("GZ-psycholoog", default=False),
                    "In opleiding": st.column_config.CheckboxColumn(
                        "In opleiding (PIOG)", default=False, help="Productiviteit telt voor 75%: inzetbaar = 75% van de netto uren."
                    ),
                    "Contracturen": uren_kolom(
                        "🔒 Contracturen", disabled=True, help="Aanpassen in de tabel Uren hieronder."
                    ),
                    "Inzetbare tijd": uren_kolom(
                        "🔒 Inzetbaar", disabled=True,
                        help="Contracturen min de aftrekposten hieronder; daarvan 75% bij een PIOG; 0 bij zwangerschapsverlof. Wordt bijgewerkt na opslaan.",
                    ),
                    **{d: st.column_config.CheckboxColumn(DAG_LABELS[d], default=False, help="Werkt op deze dag. Telt mee voor de MDO's: iemand wordt alleen ingepland op een afdeling als die op de MDO-dag werkt.") for d in WERKDAGEN},
                },
            )
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                _opslaan(wijzig_medewerkers(tabellen, naar_int(na_editor(bewerkt).drop(columns=BEREKEND), BOOL_EMPLOYEES)))

    with st.container(border=True):
        st.subheader("Uren")
        st.caption(
            "Contracturen en de uren per week die daarvan afgaan (niet beschikbaar voor de afdelingen). "
            "De inzetbare tijd wordt hieruit berekend. Medewerkers toevoegen of verwijderen doe je in de tabel hierboven."
        )
        with st.form("team_aftrek", border=False):
            bewerkt = st.data_editor(
                emp,
                key="editor_aftrek",
                num_rows="fixed",
                hide_index=True,
                width="stretch",
                height="content",
                column_order=["medewerker", "Contracturen", *AFTREK_KOLOMMEN, "zwangerschapsverlof", "Netto", "PIOG", "Inzetbare tijd"],
                column_config={
                    "medewerker": st.column_config.TextColumn("Naam", disabled=True, pinned=True),
                    "Contracturen": uren_kolom("Contracturen", required=True),
                    **{k: uren_kolom(AFTREK_LABELS[k]) for k in AFTREK_KOLOMMEN},
                    "zwangerschapsverlof": st.column_config.CheckboxColumn(
                        "Zwangerschapsverlof", help="Aangevinkt = helemaal niet inzetbaar"
                    ),
                    "Netto": uren_kolom("🔒 Netto", disabled=True, help="Contracturen min alle aftrekposten."),
                    "PIOG": st.column_config.NumberColumn(
                        f"🔒 PIOG −{1 - PIOG_PRODUCTIVITEIT:.0%}", disabled=True, format="localized",
                        help=f"Iemand in opleiding telt voor {PIOG_PRODUCTIVITEIT:.0%} productiviteit: "
                        f"er gaat {1 - PIOG_PRODUCTIVITEIT:.0%} van de netto uren af. Leeg = geen PIOG.",
                    ),
                    "Inzetbare tijd": uren_kolom(
                        "🔒 Inzetbaar", disabled=True,
                        help="Netto + PIOG-correctie (0 bij zwangerschapsverlof). Wordt bijgewerkt na opslaan.",
                    ),
                },
            )
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                _opslaan(wijzig_medewerkers(tabellen, naar_int(na_editor(bewerkt).drop(columns=BEREKEND), BOOL_EMPLOYEES)))


# ------------------------------------------------------------------ Afdelingen & locaties


def _tab_afdelingen(tabellen) -> None:
    _E, _D, L, _G = namen(tabellen)

    with st.container(border=True):
        st.subheader("Afdelingen")
        st.caption(
            "Per afdeling en locatie: hoeveel uur psycholoog er per week nodig is. Een afdeling die op twee locaties "
            "zit, staat er twee keer in. Hernoemen of verwijderen werkt door in de huidige planning en de extra eisen."
        )
        with st.expander("Uitleg van de kolommen"):
            st.markdown(
                "- **MDO-dag**: de dag van het MDO. Alleen medewerkers die op die dag werken, kunnen op deze "
                "afdeling worden ingepland (leeg = geen MDO).\n"
                "- **Alleen gz**: alleen gz-psychologen mogen hier werken.\n"
                "- **Max. medewerkers**: hoeveel verschillende medewerkers er maximaal op deze afdeling mogen (leeg = geen maximum).\n"
                "- **Afdelingsgroep**: afdelingen met hetzelfde label (bv. *GRZ*) doet een medewerker op één locatie.\n\n"
                + UITLEG_RIJEN
            )
        req = naar_bool(tabellen["Requirements"], ["alleen_gz_psycholoog"])
        req = voor_editor(req, ["afdeling", "locatie"])
        with st.form("afdelingen", border=False):
            bewerkt = st.data_editor(
                req,
                key="editor_afdelingen",
                num_rows="add",
                hide_index=True,
                width="stretch",
                height="content",
                column_order=[VERWIJDER, "afdeling", "locatie", "vereiste_uren", "mdo_dag", "alleen_gz_psycholoog", "max_medewerkers", "afdelingsgroep"],
                column_config={
                    VERWIJDER: verwijder_kolom(),
                    "afdeling": st.column_config.TextColumn("Afdeling", required=True, pinned=True, width="large"),
                    "locatie": st.column_config.SelectboxColumn("Locatie", options=L, required=True, width="small"),
                    "vereiste_uren": uren_kolom("Uren per week", required=True, width="small"),
                    "mdo_dag": st.column_config.SelectboxColumn(
                        "MDO-dag", options=WERKDAGEN, format_func=lambda d: DAG_NAMEN.get(d, d).capitalize(),
                        help="Dag van het MDO; leeg = geen MDO", width="small",
                    ),
                    "alleen_gz_psycholoog": st.column_config.CheckboxColumn("Alleen gz", default=False, width="small"),
                    "max_medewerkers": st.column_config.NumberColumn("Max. medewerkers", min_value=1, step=1, width="small"),
                    "afdelingsgroep": st.column_config.TextColumn("Afdelingsgroep", width="small"),
                },
            )
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                _opslaan(wijzig_vereisten(tabellen, naar_int(na_editor(bewerkt), ["alleen_gz_psycholoog"])))

    with st.container(border=True):
        st.subheader("Reizen beperken")
        st.caption("De planning probeert te voorkomen dat iemand op béide locaties werkt.")
        config = tabellen["Config"].set_index("key")["value"].to_dict()
        idx = lambda w, standaard: L.index(w) if w in L else standaard  # noqa: E731
        with st.form("reispaar", border=False):
            c1, c2, _ruimte = st.columns([1, 1, 2])
            l1 = c1.selectbox("Locatie 1", L, index=idx(config.get("locatie_1"), 0))
            l2 = c2.selectbox("Locatie 2", L, index=idx(config.get("locatie_2"), min(1, len(L) - 1)))
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                if l1 == l2:
                    st.error("Kies twee verschillende locaties.")
                else:
                    t = dict(tabellen)
                    t["Config"] = pd.DataFrame({"key": ["locatie_1", "locatie_2"], "value": [l1, l2]})
                    _opslaan((t, [], []))


# ------------------------------------------------------------------ Huidige planning


def _tab_huidige_planning(tabellen, inzetbaar: pd.Series) -> None:
    E, _D, _L, _G = namen(tabellen)
    req = tabellen["Requirements"]
    plekken = {_plek(d, l): (d, l) for d, l in sorted(zip(req["afdeling"], req["locatie"]), key=lambda p: (p[0].lower(), p[1]))}

    cs = tabellen["CurrentSchedule"].copy()
    cs["plek"] = [_plek(d, l) for d, l in zip(cs["afdeling"], cs["locatie"])]
    cs = voor_editor(cs, ["medewerker", "plek"])

    with st.container(border=True):
        st.subheader("Wie werkt nu waar?")
        st.caption(
            "De planning zoals die nu is. Een nieuwe planning wijkt hier zo weinig mogelijk van af. "
            "Kies de medewerker en de afdeling uit de lijst."
        )
        with st.expander("Hoe voeg ik rijen toe of verwijder ik ze?"):
            st.markdown(UITLEG_RIJEN)
        with st.form("huidige_planning", border=False):
            bewerkt = st.data_editor(
                cs,
                key="editor_huidig",
                num_rows="add",
                hide_index=True,
                width="stretch",
                height="content",
                column_order=[VERWIJDER, "medewerker", "plek", "huidige_uren"],
                column_config={
                    VERWIJDER: verwijder_kolom(),
                    "medewerker": st.column_config.SelectboxColumn("Medewerker", options=E, required=True),
                    "plek": st.column_config.SelectboxColumn("Afdeling (locatie)", options=list(plekken), required=True),
                    "huidige_uren": uren_kolom("Uren per week", required=True),
                },
            )
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                onbekend = sorted({p for p in bewerkt["plek"].dropna() if p not in plekken})
                if onbekend:
                    st.error(f"Deze afdelingen bestaan niet (meer): {', '.join(onbekend)}. Kies een afdeling uit de lijst.")
                else:
                    bewerkt = na_editor(bewerkt)
                    bewerkt = bewerkt[bewerkt["plek"].notna()]
                    bewerkt["afdeling"] = [plekken[p][0] for p in bewerkt["plek"]]
                    bewerkt["locatie"] = [plekken[p][1] for p in bewerkt["plek"]]
                    t = dict(tabellen)
                    t["CurrentSchedule"] = bewerkt.drop(columns="plek")
                    _opslaan((t, [], []))

    with st.expander("Controle van de huidige planning", icon=":material/fact_check:"):
        cs = tabellen["CurrentSchedule"]
        uren_cs = pd.to_numeric(cs["huidige_uren"], errors="coerce").fillna(0)
        links, rechts = st.columns(2, gap="large")

        with links:
            st.markdown("**Per medewerker**")
            st.caption("Negatief verschil: iemand staat nu voor meer uren ingepland dan inzetbaar.")
            totaal = uren_cs.groupby(cs["medewerker"]).sum()
            controle = pd.DataFrame({"Medewerker": tabellen["Employees"]["medewerker"], "Inzetbaar": inzetbaar.values})
            controle["Nu ingepland"] = controle["Medewerker"].map(totaal).fillna(0)
            controle["Verschil"] = controle["Inzetbaar"] - controle["Nu ingepland"]
            st.dataframe(
                controle.sort_values("Medewerker").style.format(nl_getal, subset=["Inzetbaar", "Nu ingepland", "Verschil"]).map(
                    lambda v: "color: #c0392b; font-weight: 600" if v < 0 else "", subset=["Verschil"]
                ),
                hide_index=True,
                width="stretch",
                height="content",
            )

        with rechts:
            st.markdown("**Per afdeling**")
            st.caption("Negatief verschil: er staan nu minder uren ingepland dan de afdeling nodig heeft.")
            req = tabellen["Requirements"]
            per_dl = uren_cs.groupby([cs["afdeling"], cs["locatie"]]).sum()
            afd = pd.DataFrame({
                "Afdeling": req["afdeling"],
                "Locatie": req["locatie"],
                "Vereist": pd.to_numeric(req["vereiste_uren"], errors="coerce").fillna(0),
            })
            afd["Nu ingepland"] = [per_dl.get((d, l), 0.0) for d, l in zip(afd["Afdeling"], afd["Locatie"])]
            afd["Verschil"] = afd["Nu ingepland"] - afd["Vereist"]
            st.dataframe(
                afd.sort_values(["Afdeling", "Locatie"]).style.format(nl_getal, subset=["Vereist", "Nu ingepland", "Verschil"]).map(
                    lambda v: "color: #c0392b; font-weight: 600" if v < 0 else "", subset=["Verschil"]
                ),
                hide_index=True,
                width="stretch",
                height="content",
            )


# ------------------------------------------------------------------ Extra eisen


def _tab_extra_eisen(tabellen) -> None:
    E, D, L, G = namen(tabellen)
    with st.container(border=True):
        st.subheader("Minimumuren per medewerker")
        st.caption(
            "Vaste afspraken voor deze periode, bijvoorbeeld *'minimaal 4 uur AGT op LH'*. "
            "Vul óf een afdeling óf een afdelingsgroep in. Locatie leeg = maakt niet uit. "
            "Uren leeg = moet er werken, aantal maakt niet uit."
        )
        with st.expander("Hoe voeg ik rijen toe of verwijder ik ze?"):
            st.markdown(UITLEG_RIJEN)
        with st.form("minimumuren", border=False):
            bewerkt = st.data_editor(
                voor_editor(tabellen["MinimumUren"], ["medewerker"]),
                key="editor_minimum",
                num_rows="add",
                hide_index=True,
                width="stretch",
                height="content",
                column_order=[VERWIJDER, "medewerker", "afdeling", "afdelingsgroep", "locatie", "uren"],
                column_config={
                    VERWIJDER: verwijder_kolom(),
                    "medewerker": st.column_config.SelectboxColumn("Medewerker", options=E, required=True),
                    "afdeling": st.column_config.SelectboxColumn("Afdeling", options=D),
                    "afdelingsgroep": st.column_config.SelectboxColumn("Of: afdelingsgroep", options=G),
                    "locatie": st.column_config.SelectboxColumn("Locatie", options=L),
                    "uren": uren_kolom("Minimaal uren"),
                },
            )
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                beide = bewerkt["afdeling"].notna() & bewerkt["afdelingsgroep"].notna()
                geen = bewerkt["afdeling"].isna() & bewerkt["afdelingsgroep"].isna()
                if (beide | geen).any():
                    st.error("Vul per regel óf een afdeling óf een afdelingsgroep in (niet allebei, niet geen van beide).")
                else:
                    t = dict(tabellen)
                    t["MinimumUren"] = na_editor(bewerkt)
                    _opslaan((t, [], []))
    st.caption("Iemand vastzetten op of uitsluiten van een afdeling doe je op de pagina **Bijsturen**.")


# ------------------------------------------------------------------ Back-up


def _backup(tabellen) -> None:
    with st.expander("Back-up van de invoer", icon=":material/backup:"):
        st.markdown(
            "Download alle invoer (team, uren, afdelingen, huidige planning, extra eisen, bijsturing en instellingen) "
            "als één Excel-bestand. Bewaar dat bijvoorbeeld op OneDrive."
        )
        st.download_button(
            "Download back-up (Excel)", icon=":material/download:",
            data=excel_bytes({naam: tabellen[naam] for naam in TABELLEN}),
            file_name=f"planning_invoer_{date.today():%Y-%m-%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.divider()
        st.markdown("**Back-up terugzetten**")
        st.caption("Gebruik alleen een bestand dat je hier eerder hebt gedownload. Dit **vervangt alle huidige invoer**.")
        bestand = st.file_uploader("Back-up-bestand", type=["xlsx"], label_visibility="collapsed")
        zeker = st.checkbox("Ik begrijp dat alle huidige invoer wordt vervangen")
        if st.button("Terugzetten", disabled=bestand is None or not zeker or achtergrond.bezig()):
            try:
                nieuw = load_excel_tables(bestand)
            except Exception as fout:  # noqa: BLE001 - leesfout tonen aan de gebruiker
                st.error(f"Kon het bestand niet lezen: {fout}")
                return
            if nieuw["Employees"].empty or nieuw["Requirements"].empty:
                st.error("Dit bestand bevat geen medewerkers of afdelingen; het lijkt geen back-up uit deze app.")
                return
            store.schrijf_tabellen(nieuw)
            store.wis_resultaat()
            st.session_state["meldingen"] = [f"Back-up teruggezet: {len(nieuw['Employees'])} medewerkers, {len(nieuw['Requirements'])} afdelingen."]
            st.rerun()
