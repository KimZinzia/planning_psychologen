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
    KOLOMMEN,
    PIOG_PRODUCTIVITEIT,
    REGELS,
    TABELLEN,
    WERKDAGEN,
    bereken_inzetbare_tijd,
    bereken_uren_opbouw,
    is_basispsycholoog,
    is_piog,
    load_excel_tables,
    WEKEN,
    lees_tijd,
    mdo_dagen,
    tijd_tekst,
    params_from_tables,
    valideer,
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

WEKEN_LABELS = {"elke": "Elke week", "even": "Even weken", "oneven": "Oneven weken"}
TIJD_PATROON = r"^\s*([01]?\d|2[0-3])([:.][0-5]\d)?\s*$"

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
                    **{d: st.column_config.CheckboxColumn(DAG_LABELS[d], default=False, help="Werkt op deze dag. Telt mee voor de IDO's: iemand wordt alleen ingepland op een afdeling als die bij een IDO van die afdeling kan zijn.") for d in WERKDAGEN},
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
            "zit, staat er twee keer in. Hernoemen of verwijderen werkt door in de huidige planning en de afspraken."
        )
        with st.expander("Uitleg van de kolommen"):
            st.markdown(
                "- **Alleen gz**: alleen gz-psychologen mogen hier werken.\n"
                "- **Max. medewerkers**: hoeveel verschillende medewerkers er maximaal op deze afdeling mogen (leeg = geen maximum).\n"
                "- **Afdelingsgroep**: label (bv. *GRZ*) om afdelingen te groeperen, zodat een afspraak voor de hele groep kan gelden.\n"
                "- **Voorrang bij tekort**: is er te weinig formatie, dan wordt deze afdeling zo min mogelijk gekort.\n\n"
                + UITLEG_RIJEN
            )
        req = naar_bool(tabellen["Requirements"], ["alleen_gz_psycholoog", "voorrang"])
        req = voor_editor(req, ["afdeling", "locatie"])
        with st.form("afdelingen", border=False):
            bewerkt = st.data_editor(
                req,
                key="editor_afdelingen",
                num_rows="add",
                hide_index=True,
                width="stretch",
                height="content",
                column_order=[VERWIJDER, "afdeling", "locatie", "vereiste_uren", "alleen_gz_psycholoog", "max_medewerkers", "afdelingsgroep", "voorrang"],
                column_config={
                    VERWIJDER: verwijder_kolom(),
                    "afdeling": st.column_config.TextColumn("Afdeling", required=True, pinned=True, width="large"),
                    "locatie": st.column_config.SelectboxColumn("Locatie", options=L, required=True, width="small"),
                    "vereiste_uren": uren_kolom("Uren per week", required=True, width="small"),
                    "alleen_gz_psycholoog": st.column_config.CheckboxColumn("Alleen gz", default=False, width="small"),
                    "max_medewerkers": st.column_config.NumberColumn("Max. medewerkers", min_value=1, step=1, width="small"),
                    "afdelingsgroep": st.column_config.TextColumn("Afdelingsgroep", width="small"),
                    "voorrang": st.column_config.CheckboxColumn(
                        "Voorrang bij tekort", default=False, width="small",
                        help="Is er te weinig formatie, dan wordt deze afdeling zo min mogelijk gekort (bv. hospice).",
                    ),
                },
            )
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                nieuw = naar_int(na_editor(bewerkt), ["alleen_gz_psycholoog", "voorrang"])
                _opslaan(wijzig_vereisten(tabellen, nieuw))

    _ido(tabellen)


def _ido(tabellen) -> None:
    req = tabellen["Requirements"]
    plekken = {_plek(d, l): (d, l) for d, l in sorted(zip(req["afdeling"], req["locatie"]), key=lambda p: (p[0].lower(), p[1]))}
    with st.container(border=True):
        st.subheader("IDO's")
        st.caption(
            "Wanneer elke afdeling IDO heeft. Iemand wordt alleen op een afdeling ingepland als die bij minstens één "
            "IDO van die afdeling kan zijn, en niemand hoeft bij twee IDO's tegelijk te zijn."
        )
        with st.expander("Uitleg van de kolommen"):
            st.markdown(
                "- **Afdeling**: één rij per IDO. Heeft een afdeling meerdere onderdelen met elk een eigen IDO, "
                "zet ze dan allemaal op een eigen rij (met de naam bij *Onderdeel*). Onderdelen met dezelfde dag en "
                "tijd tellen als één IDO.\n"
                "- **Weken**: elke week, of alleen in even of oneven weken. Een IDO in even weken botst niet met "
                "een IDO in oneven weken.\n"
                "- **Van / tot**: de tijd, bv. *12:30* en *15:00*. Leeg = tijd onbekend (dan telt alleen de dag).\n"
                "- **Samen**: twee afdelingen die samen één IDO hebben, krijgen hier hetzelfde label (bv. "
                "*Bultweide + Gemeent*). Wie op allebei werkt, kan dan bij die ene IDO zijn.\n\n"
                "Regels: bij elke IDO is een psycholoog van de afdeling; wie op de afdeling werkt, is bij minstens "
                "één IDO van die afdeling (uitzondering: afspraak *Hoeft niet bij IDO*); heeft een afdeling meerdere "
                "IDO's, dan liefst een gz-psycholoog bij allemaal.\n\n"
                + UITLEG_RIJEN
            )
        ido = tabellen["IDO"].copy()
        ido["plek"] = [_plek(d, l) for d, l in zip(ido["afdeling"], ido["locatie"])]
        ido["dag"] = ido["dag"].map(lambda w: (mdo_dagen(w) or (None,))[0])
        ido = voor_editor(ido, ["plek"])
        with st.form("ido", border=False):
            bewerkt = st.data_editor(
                ido,
                key="editor_ido",
                num_rows="add",
                hide_index=True,
                width="stretch",
                height="content",
                column_order=[VERWIJDER, "plek", "onderdeel", "dag", "weken", "van", "tot", "samen"],
                column_config={
                    VERWIJDER: verwijder_kolom(),
                    "plek": st.column_config.SelectboxColumn("Afdeling", options=list(plekken), required=True, pinned=True, width="medium"),
                    "onderdeel": st.column_config.TextColumn("Onderdeel", width="medium"),
                    "dag": st.column_config.SelectboxColumn(
                        "Dag", options=WERKDAGEN, format_func=lambda d: DAG_NAMEN.get(d, d).capitalize(), required=True, width="small",
                    ),
                    "weken": st.column_config.SelectboxColumn(
                        "Weken", options=WEKEN, format_func=WEKEN_LABELS.get, default="elke", required=True, width="small",
                    ),
                    "van": st.column_config.TextColumn("Van", help="Bv. 12:30", width="small", validate=TIJD_PATROON),
                    "tot": st.column_config.TextColumn("Tot", help="Bv. 15:00", width="small", validate=TIJD_PATROON),
                    "samen": st.column_config.TextColumn("Samen", help="Zelfde label = één gezamenlijke IDO", width="medium"),
                },
            )
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                nieuw = na_editor(bewerkt)
                fouten = []
                if nieuw["plek"].isna().any() or nieuw["dag"].isna().any():
                    fouten.append("Elke IDO moet een afdeling en een dag hebben.")
                for r in nieuw.to_dict("records"):
                    van, tot = lees_tijd(r["van"]), lees_tijd(r["tot"])
                    if (van is None) != (tot is None) or (van is not None and van >= tot):
                        fouten.append(f"{r['plek']}: vul een begin- én eindtijd in (bv. 12:30 en 15:00), begin vóór eind.")
                nieuw["afdeling"] = nieuw["plek"].map(lambda p: plekken.get(p, (None, None))[0])
                nieuw["locatie"] = nieuw["plek"].map(lambda p: plekken.get(p, (None, None))[1])
                nieuw["van"] = nieuw["van"].map(lambda w: tijd_tekst(lees_tijd(w)) or None)
                nieuw["tot"] = nieuw["tot"].map(lambda w: tijd_tekst(lees_tijd(w)) or None)
                _opslaan(({**tabellen, "IDO": nieuw[KOLOMMEN["IDO"]]}, ["IDO's opgeslagen"], fouten))


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


# Keuzes in de kolom 'Regel', zoals de gebruiker ze ziet.
REGEL_LABELS = {
    "minimaal": "Minstens … uur", "precies": "Precies … uur", "uitsluiten": "Werkt hier niet",
    "geen_ido": "Hoeft niet bij IDO",
}


def _groep_label(groep: str) -> str:
    return f"Alle {groep}-afdelingen"


def _tab_extra_eisen(tabellen) -> None:
    _afspraken(tabellen)
    _reizen_beperken(tabellen)
    _werkdruk_basis(tabellen)


def _werkdruk_basis(tabellen) -> None:
    p = params_from_tables(tabellen)
    basis = [e for e in p.E if p.avail[e] > 0 and is_basispsycholoog(p, e)]
    piog = [e for e in p.E if p.avail[e] > 0 and is_piog(p, e)]
    with st.container(border=True):
        st.subheader("Werkdruk")
        st.caption(
            "Hoe vol medewerkers liefst worden ingepland, als percentage van hun inzetbare uren. Dit zijn voorkeuren, "
            "geen harde grenzen: als het niet anders kan, wijkt de planning ervan af, zo weinig mogelijk. "
            "Het maximum voor masterpsychologen en het maximum aantal afdelingen wegen het zwaarst: "
            "die gaan vóór de andere doelen. "
            f"Masterpsycholoog = geen gz en niet in opleiding (nu: {', '.join(basis) if basis else 'niemand'}). "
            f"PIOG = in opleiding (nu: {', '.join(piog) if piog else 'niemand'}); hun inzetbare uren zijn al "
            f"gecorrigeerd voor de {PIOG_PRODUCTIVITEIT:.0%} productiviteit."
        )
        with st.form("werkdruk_basis", border=False):
            c1, c2, c3, c4 = st.columns(4)
            gz = c1.number_input(
                "Gz-psychologen: streven naar (%)", min_value=50, max_value=100, step=5,
                value=int(round(p.streef_bezetting_gz * 100)),
            )
            minimaal = c2.number_input(
                "Masterpsychologen: liefst minimaal (%)", min_value=0, max_value=100, step=5,
                value=int(round(p.min_bezetting_niet_gz * 100)),
            )
            maximaal = c3.number_input(
                "Masterpsychologen: liefst maximaal (%)", min_value=0, max_value=100, step=5,
                value=int(round(p.max_bezetting_niet_gz * 100)),
            )
            pct = c4.number_input(
                "Masterpsychologen: maximaal, weegt zwaar (%)", min_value=50, max_value=100, step=5,
                value=int(round(p.max_bezetting_basis * 100)),
            )
            c5, c6, _r2, _r3 = st.columns(4)
            streef_piog = c5.number_input(
                "PIOG: streven naar (%)", min_value=50, max_value=100, step=5,
                value=int(round(p.streef_bezetting_piog * 100)),
            )
            max_afd = c6.number_input(
                "Liefst maximaal … afdelingen per medewerker", min_value=0, max_value=20, step=1,
                value=int(p.max_afdelingen), help="0 = geen maximum.",
            )
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                if minimaal > maximaal:
                    st.error("Bij masterpsychologen moet het minimum lager zijn dan het maximum.")
                    st.stop()
                config = tabellen["Config"].set_index("key")["value"].to_dict()
                nieuw = {**config, "max_bezetting_basis": str(int(pct)), "streef_bezetting_gz": str(int(gz)),
                         "streef_bezetting_piog": str(int(streef_piog)),
                         "min_bezetting_niet_gz": str(int(minimaal)), "max_bezetting_niet_gz": str(int(maximaal)),
                         "max_afdelingen": str(int(max_afd))}
                # Alleen de instellingen wegschrijven; de rest van de invoer blijft onaangeroerd.
                store.schrijf_tabel("Config", pd.DataFrame({"key": list(nieuw), "value": list(nieuw.values())}))
                st.session_state["meldingen"] = ["Opgeslagen"]
                st.rerun()


def _leeg(w) -> bool:
    return w is None or (not isinstance(w, str) and pd.isna(w)) or (isinstance(w, str) and not w.strip())


def _zin(rij) -> tuple[str, str | None]:
    """Eén afspraak als gewone zin, plus een eventuele fout in die regel."""
    e, regel, uren = rij["medewerker"], rij["regel"], rij["uren"]
    d, g, l = rij["afdeling"], rij["afdelingsgroep"], rij["locatie"]
    if _leeg(e):
        return "", "kies een medewerker"
    if not _leeg(d):
        plek = f"afdeling {d}" + (f" op {l}" if not _leeg(l) else "")
    elif not _leeg(g):
        plek = f"de {g}-afdelingen" + (f" op {l}" if not _leeg(l) else "")
    elif not _leeg(l):
        plek = f"locatie {l} (alle afdelingen daar)"
    else:
        return f"**{e}**", "kies een locatie, een afdeling of allebei"
    u = None if _leeg(uren) else nl_getal(float(uren))
    if regel == "uitsluiten":
        return f"**{e}** werkt niet op {plek}", None
    if regel == "geen_ido":
        return f"**{e}** hoeft niet bij de IDO van {plek} te zijn (als {e} daar werkt)", None
    if regel == "precies":
        if u is None:
            return f"**{e}** werkt precies … uur op {plek}", "vul het aantal uren in"
        return f"**{e}** werkt precies {u} uur op {plek}", None
    if u is None:
        return f"**{e}** werkt op {plek} (aantal uren maakt niet uit)", None
    return f"**{e}** werkt minstens {u} uur op {plek}", None


def _afspraken(tabellen) -> None:
    E, D, L, G = namen(tabellen)
    groep_van = {_groep_label(g): g for g in G}
    with st.container(border=True):
        st.subheader("Afspraken per medewerker")
        st.caption(
            "Lees elke regel als een zin: *Anna · minstens … uur · 4 · ONO · AGT* = Anna werkt minstens 4 uur op AGT "
            "op ONO. Onder de tabel staat hoe de planning elke regel begrijpt."
        )
        with st.expander("Uitleg en voorbeelden"):
            st.markdown(
                "- **Minstens … uur**: werkt daar minstens zoveel uur. Uren leeg = moet er werken, aantal maakt niet uit.\n"
                "- **Precies … uur**: werkt daar precies zoveel uur.\n"
                "- **Werkt hier niet**: wordt daar nooit ingepland.\n"
                "- **Hoeft niet bij IDO**: mag daar werken zonder bij de IDO te zijn (bv. omdat die niet op de "
                "IDO-dag werkt). Combineer met *Minstens … uur* als iemand er ook echt moet werken.\n\n"
                "**Waar** vul je in met Locatie en Afdeling:\n"
                "- alleen een **afdeling**: die afdeling, op welke locatie ook;\n"
                "- alleen een **locatie**: de hele locatie, alle afdelingen daar;\n"
                "- **allebei**: die afdeling op die locatie;\n"
                "- **Alle GRZ-afdelingen** (onderaan de keuzelijst): alle afdelingen met dat label samen.\n\n"
                + UITLEG_RIJEN.replace("Klik daarna op **Opslaan**", "Klik daarna op **Afspraken opslaan**")
            )

        df = tabellen["Afspraken"].copy()
        df["waar"] = [
            d if pd.notna(d) else (_groep_label(g) if pd.notna(g) else None)
            for d, g in zip(df["afdeling"], df["afdelingsgroep"])
        ]
        # Bewust geen formulier: dan werkt de vertaling onder de tabel meteen mee.
        bewerkt = st.data_editor(
            voor_editor(df, ["medewerker"]),
            key="editor_afspraken",
            num_rows="add",
            hide_index=True,
            width="stretch",
            height="content",
            column_order=[VERWIJDER, "medewerker", "regel", "uren", "locatie", "waar"],
            column_config={
                VERWIJDER: verwijder_kolom(),
                "medewerker": st.column_config.SelectboxColumn("Medewerker", options=E, required=True),
                "regel": st.column_config.SelectboxColumn(
                    "Regel", options=REGELS, format_func=REGEL_LABELS.get, required=True, default="minimaal",
                ),
                "uren": uren_kolom("Uren", help="Bij 'minstens': leeg = moet er werken. Bij 'precies': verplicht. Bij 'werkt hier niet' en 'hoeft niet bij IDO': leeg."),
                "locatie": st.column_config.SelectboxColumn("Locatie", options=L, help="Leeg = op alle locaties"),
                "waar": st.column_config.SelectboxColumn(
                    "Afdeling  (leeg = hele locatie)", options=[*D, *groep_van], width="large",
                    help="Leeg = de hele locatie. Onderaan de lijst staan de afdelingsgroepen, bv. 'Alle GRZ-afdelingen'.",
                ),
            },
        )

        nieuw = na_editor(bewerkt).sort_index()  # oorspronkelijke volgorde bewaren
        waar = nieuw.pop("waar")
        nieuw["afdeling"] = waar.map(lambda w: None if _leeg(w) or w in groep_van else w)
        nieuw["afdelingsgroep"] = waar.map(lambda w: groep_van.get(w) if isinstance(w, str) else None)
        nieuw.loc[nieuw["regel"].isin(["uitsluiten", "geen_ido"]), "uren"] = None
        nieuw = nieuw[KOLOMMEN["Afspraken"]]

        st.markdown("**Zo begrijpt de planning je afspraken**")
        zinnen = [_zin(r) for r in nieuw.sort_values("medewerker", key=lambda c: c.astype(str).str.lower()).to_dict("records")]
        if not zinnen:
            st.caption("Nog geen afspraken.")
        regels_md = []
        for zin, fout in zinnen:
            if fout:
                regels_md.append(f"- :red[:material/error:] {zin} — :red[{fout}]")
            else:
                regels_md.append(f"- {zin}")
        if regels_md:
            st.markdown("\n".join(regels_md))

        gewijzigd = not nieuw.reset_index(drop=True).fillna("").astype(str).equals(
            tabellen["Afspraken"][KOLOMMEN["Afspraken"]].reset_index(drop=True).fillna("").astype(str)
        )
        c1, c2 = st.columns([1, 3], vertical_alignment="center")
        opslaan = c1.button("Afspraken opslaan", type="primary", disabled=achtergrond.bezig() or not gewijzigd, width="stretch")
        c2.caption(":orange[:material/edit: Niet opgeslagen wijzigingen]" if gewijzigd else "Alles is opgeslagen.")
        if opslaan:
            fouten = [f for f in valideer(params_from_tables({**tabellen, "Afspraken": nieuw})) if f.startswith("Afspraken")]
            if fouten:
                for fout in fouten:
                    st.error(fout)
            elif achtergrond.bezig():
                st.error("Er wordt een planning berekend; wijzigingen kun je opslaan als die klaar is.")
            else:
                # Alleen de afspraken wegschrijven; de rest van de invoer blijft onaangeroerd.
                store.schrijf_tabel("Afspraken", nieuw)
                st.session_state.pop("editor_afspraken", None)
                st.session_state["meldingen"] = ["Afspraken opgeslagen. Klik op de pagina Planning op **Plan maken** om ze mee te nemen."]
                st.rerun()


def _reizen_beperken(tabellen) -> None:
    L = namen(tabellen)[2]
    with st.container(border=True):
        st.subheader("Locaties combineren")
        config = tabellen["Config"].set_index("key")["value"].to_dict()
        idx = lambda w, standaard: L.index(w) if w in L else standaard  # noqa: E731
        GEEN = "(geen)"
        met_geen = [GEEN, *L]
        with st.form("reispaar", border=False):
            st.markdown("**Nooit combineren**")
            st.caption("Harde regel: niemand wordt op béide locaties ingepland, ook niet als dat meer wijzigingen kost.")
            c1, c2, _ruimte = st.columns([1, 1, 2])
            n1 = c1.selectbox("Locatie", met_geen, index=met_geen.index(config.get("nooit_1")) if config.get("nooit_1") in L else 0, key="nooit_1")
            n2 = c2.selectbox("Locatie", met_geen, index=met_geen.index(config.get("nooit_2")) if config.get("nooit_2") in L else 0, key="nooit_2")
            st.markdown("**Liever niet combineren**")
            st.caption("Alleen als het echt niet anders kan: de planning houdt het aantal medewerkers op béide locaties zo laag mogelijk.")
            c1, c2, _ruimte = st.columns([1, 1, 2])
            l1 = c1.selectbox("Locatie", L, index=idx(config.get("locatie_1"), 0), key="liever_1")
            l2 = c2.selectbox("Locatie", L, index=idx(config.get("locatie_2"), min(1, len(L) - 1)), key="liever_2")
            st.markdown("**Altijd samen**")
            st.caption("Harde regel: wie op de eerste locatie werkt, werkt ook op de tweede (bv. Rijnhof alleen samen met ONO).")
            c1, c2, _ruimte = st.columns([1, 1, 2])
            s1 = c1.selectbox("Wie werkt op", met_geen, index=met_geen.index(config.get("samen_1")) if config.get("samen_1") in L else 0, key="samen_1")
            s2 = c2.selectbox("werkt ook op", met_geen, index=met_geen.index(config.get("samen_2")) if config.get("samen_2") in L else 0, key="samen_2")
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                nooit_leeg = n1 == GEEN and n2 == GEEN
                samen_leeg = s1 == GEEN and s2 == GEEN
                if l1 == l2:
                    st.error("Kies bij 'liever niet combineren' twee verschillende locaties.")
                elif not nooit_leeg and (GEEN in (n1, n2) or n1 == n2):
                    st.error("Kies bij 'nooit combineren' twee verschillende locaties, of twee keer '(geen)'.")
                elif {n1, n2} == {l1, l2}:
                    st.error("Hetzelfde paar staat bij 'nooit' én bij 'liever niet'; kies er één.")
                elif not samen_leeg and (GEEN in (s1, s2) or s1 == s2):
                    st.error("Kies bij 'altijd samen' twee verschillende locaties, of twee keer '(geen)'.")
                elif not samen_leeg and not nooit_leeg and {s1, s2} == {n1, n2}:
                    st.error("Hetzelfde paar staat bij 'nooit' én bij 'altijd samen'; kies er één.")
                else:
                    nieuw = {**config, "locatie_1": l1, "locatie_2": l2,
                             "nooit_1": None if nooit_leeg else n1, "nooit_2": None if nooit_leeg else n2,
                             "samen_1": None if samen_leeg else s1, "samen_2": None if samen_leeg else s2}
                    # Alleen de instellingen wegschrijven; de rest van de invoer blijft onaangeroerd.
                    store.schrijf_tabel("Config", pd.DataFrame({"key": list(nieuw), "value": list(nieuw.values())}))
                    st.session_state["meldingen"] = ["Opgeslagen"]
                    st.rerun()


# ------------------------------------------------------------------ Back-up


def _backup(tabellen) -> None:
    with st.expander("Back-up van de invoer", icon=":material/backup:"):
        st.markdown(
            "Download alle invoer (team, uren, afdelingen, huidige planning, afspraken en instellingen) "
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
