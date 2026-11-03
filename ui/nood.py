"""Pagina 'Noodplanning': tijdelijke aanpassingen als de formatie krap is,
bijvoorbeeld door verzuim. Elke aanpassing heeft een einddatum (t/m); daarna
geldt vanzelf weer de gewone invoer. De basisinvoer blijft onaangeroerd.

Hoe de aanpassingen worden toegepast staat in data.pas_tijdelijk_toe.
"""

from datetime import date

import pandas as pd
import streamlit as st

import achtergrond
import store
from data import LOSLAATBARE_REGELS, lees_datum, params_from_tables
from ui.helpers import VERWIJDER, na_editor, namen, nl_getal, verwijder_kolom, voor_editor
from ui.stijl import paginakop


def _status(tot) -> str:
    d = lees_datum(tot)
    if d is None:
        return "geen einddatum"
    return f"actief t/m {d:%d-%m-%Y}" if d >= date.today() else "verlopen"


def _naar_editor(df: pd.DataFrame, sorteer_op: list[str]) -> pd.DataFrame:
    df = df.copy()
    df["tot"] = df["tot"].map(lees_datum)
    df["status"] = df["tot"].map(_status)
    return voor_editor(df, sorteer_op)


def _na_editor(bewerkt: pd.DataFrame, kolommen: list[str]) -> pd.DataFrame:
    df = na_editor(bewerkt).sort_index()
    df["tot"] = df["tot"].map(lambda d: d.isoformat() if isinstance(d, date) else None)
    return df[kolommen]


def _bewaar(tabel: str, df: pd.DataFrame, fouten: list[str]) -> None:
    if achtergrond.bezig():
        fouten = ["Er wordt een planning berekend; wijzigingen kun je opslaan als die klaar is.", *fouten]
    if fouten:
        for f in fouten:
            st.error(f)
        return
    # Alleen deze tabel wegschrijven; de rest van de invoer blijft onaangeroerd.
    store.schrijf_tabel(tabel, df)
    st.session_state["meldingen_nood"] = ["Opgeslagen. Klik op de pagina Planning op **Plan maken** om het mee te nemen."]
    st.rerun()


def _overzicht(tabellen) -> None:
    nu = params_from_tables(tabellen)
    zonder = params_from_tables({**tabellen, "TijdelijkAfdeling": None, "TijdelijkMedewerker": None, "TijdelijkRegels": None})
    with st.container(border=True, key="kaart_nood_overzicht"):
        c1, c2, c3 = st.columns(3)
        nodig, inzet = sum(nu.req.values()), sum(nu.avail.values())
        c1.metric("Nodig per week", f"{nl_getal(nodig)} uur", delta=f"normaal {nl_getal(sum(zonder.req.values()))}",
                  delta_color="off", delta_arrow="off")
        c2.metric("Inzetbaar per week", f"{nl_getal(inzet)} uur", delta=f"normaal {nl_getal(sum(zonder.avail.values()))}",
                  delta_color="off", delta_arrow="off")
        ruimte = inzet - nodig
        c3.metric("Ruimte", f"{nl_getal(ruimte)} uur",
                  help="Negatief = tekort. De planning verdeelt een tekort dan zo eerlijk mogelijk; afdelingen met "
                  "voorrang (tabblad Afdelingen) worden zo min mogelijk gekort.")
        if nu.tijdelijk:
            st.markdown("**Nu actief**\n" + "\n".join(f"- {z}" for z in nu.tijdelijk))
        else:
            st.caption("Er zijn nu geen tijdelijke aanpassingen actief; de planning gebruikt de gewone invoer.")


def _afdelingen(tabellen) -> None:
    _E, D, L, _G = namen(tabellen)
    req = tabellen["Requirements"]
    normaal = {(d, l): u for d, l, u in zip(req["afdeling"], req["locatie"], pd.to_numeric(req["vereiste_uren"], errors="coerce"))}
    with st.container(border=True):
        st.subheader("Afdelingen: tijdelijk andere uren")
        st.caption("Bijvoorbeeld een afdeling die tijdelijk met minder uren psycholoog toe kan. Vul de uren in die tijdelijk nodig zijn.")
        df = _naar_editor(tabellen["TijdelijkAfdeling"], ["tot", "afdeling"])
        df["normaal"] = [normaal.get((d, l)) for d, l in zip(df["afdeling"], df["locatie"])]
        with st.form("nood_afdelingen", border=False):
            bewerkt = st.data_editor(
                df, key="editor_nood_afd", num_rows="add", hide_index=True, width="stretch", height="content",
                column_order=[VERWIJDER, "afdeling", "locatie", "normaal", "uren", "tot", "status", "notitie"],
                column_config={
                    VERWIJDER: verwijder_kolom(),
                    "afdeling": st.column_config.SelectboxColumn("Afdeling", options=D, required=True, width="medium"),
                    "locatie": st.column_config.SelectboxColumn("Locatie", options=L, required=True, width="small"),
                    "normaal": st.column_config.NumberColumn("Normaal", disabled=True, format="localized", width="small"),
                    "uren": st.column_config.NumberColumn("Tijdelijk", min_value=0.0, step=0.5, format="localized",
                                                          required=True, width="small"),
                    "tot": st.column_config.DateColumn("T/m", format="DD-MM-YYYY", required=True, width="small"),
                    "status": st.column_config.TextColumn("Status", disabled=True, width="medium"),
                    "notitie": st.column_config.TextColumn("Notitie", width="medium"),
                },
            )
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                nieuw = _na_editor(bewerkt, ["afdeling", "locatie", "uren", "tot", "notitie"])
                fouten = [f"{d} bestaat niet op locatie {l}." for d, l in zip(nieuw["afdeling"], nieuw["locatie"])
                          if (d, l) not in normaal]
                _bewaar("TijdelijkAfdeling", nieuw, fouten)


def _medewerkers(tabellen) -> None:
    E = namen(tabellen)[0]
    zonder = params_from_tables({**tabellen, "TijdelijkAfdeling": None, "TijdelijkMedewerker": None, "TijdelijkRegels": None})
    with st.container(border=True):
        st.subheader("Medewerkers: tijdelijk meer of minder uren")
        st.caption(
            "Bijvoorbeeld −8 bij gedeeltelijk verzuim, of +4 als iemand tijdelijk extra werkt. "
            "Dit telt op bij de inzetbare uren; het model plant nooit meer dan inzetbaar."
        )
        df = _naar_editor(tabellen["TijdelijkMedewerker"], ["tot", "medewerker"])
        df["normaal"] = df["medewerker"].map(zonder.avail)
        with st.form("nood_medewerkers", border=False):
            bewerkt = st.data_editor(
                df, key="editor_nood_med", num_rows="add", hide_index=True, width="stretch", height="content",
                column_order=[VERWIJDER, "medewerker", "normaal", "uren_erbij", "tot", "status", "notitie"],
                column_config={
                    VERWIJDER: verwijder_kolom(),
                    "medewerker": st.column_config.SelectboxColumn("Medewerker", options=E, required=True, width="medium"),
                    "normaal": st.column_config.NumberColumn("Normaal", disabled=True, format="localized", width="small",
                                                             help="Inzetbare uren zonder tijdelijke aanpassing"),
                    "uren_erbij": st.column_config.NumberColumn("Erbij", step=0.5, help="Uren erbij; negatief = minder", format="localized",
                                                                required=True, width="small"),
                    "tot": st.column_config.DateColumn("T/m", format="DD-MM-YYYY", required=True, width="small"),
                    "status": st.column_config.TextColumn("Status", disabled=True, width="medium"),
                    "notitie": st.column_config.TextColumn("Notitie", width="medium"),
                },
            )
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                _bewaar("TijdelijkMedewerker", _na_editor(bewerkt, ["medewerker", "uren_erbij", "tot", "notitie"]), [])


def _regels(tabellen) -> None:
    huidig = {r["regel"]: r for r in tabellen["TijdelijkRegels"].to_dict("records")}
    with st.container(border=True):
        st.subheader("Regels tijdelijk loslaten")
        st.caption("Alleen als het echt niet anders kan. Na de einddatum geldt de regel vanzelf weer.")
        with st.form("nood_regels", border=False):
            rijen = []
            for sleutel, label in LOSLAATBARE_REGELS.items():
                oud = huidig.get(sleutel, {})
                tot = lees_datum(oud.get("tot"))
                c1, c2, c3 = st.columns([2, 1, 2], vertical_alignment="bottom")
                aan = c1.checkbox(f"{label} tijdelijk loslaten", value=tot is not None and tot >= date.today(), key=f"nood_{sleutel}")
                datum = c2.date_input("T/m", value=tot or date.today(), format="DD-MM-YYYY", key=f"nood_tot_{sleutel}")
                notitie = c3.text_input("Notitie", value=oud.get("notitie") or "", key=f"nood_notitie_{sleutel}")
                if aan:
                    rijen.append({"regel": sleutel, "tot": datum.isoformat(), "notitie": notitie or None})
            if st.form_submit_button("Opslaan", type="primary", disabled=achtergrond.bezig()):
                _bewaar("TijdelijkRegels", pd.DataFrame(rijen, columns=["regel", "tot", "notitie"]), [])


def _opruimen(tabellen) -> None:
    verlopen = {
        t: [i for i, tot in tabellen[t]["tot"].items() if (d := lees_datum(tot)) is not None and d < date.today()]
        for t in ["TijdelijkAfdeling", "TijdelijkMedewerker", "TijdelijkRegels"]
    }
    aantal = sum(len(v) for v in verlopen.values())
    if aantal and st.button(f"Verlopen aanpassingen opruimen ({aantal})", icon=":material/delete_sweep:",
                            disabled=achtergrond.bezig()):
        for t, rijen in verlopen.items():
            if rijen:
                store.schrijf_tabel(t, tabellen[t].drop(index=rijen))
        st.session_state["meldingen_nood"] = [f"{aantal} verlopen aanpassing(en) opgeruimd."]
        st.rerun()


def pagina_nood() -> None:
    paginakop(
        "Noodplanning",
        "Tijdelijke aanpassingen als de formatie krap is, bijvoorbeeld door verzuim.<br>"
        "Elke aanpassing heeft een einddatum; daarna geldt vanzelf weer de gewone invoer.",
    )
    if not store.heeft_gegevens():
        st.info("Er is nog geen invoer.")
        return
    for melding in st.session_state.pop("meldingen_nood", []):
        st.success(melding, icon=":material/check_circle:")
    tabellen = store.lees_tabellen()
    _overzicht(tabellen)
    st.markdown(
        "Ook zonder aanpassingen komt er bij te weinig formatie een rooster: de planning verdeelt het tekort dan zo "
        "eerlijk mogelijk, en afdelingen met *voorrang bij tekort* (Invoer → Afdelingen) worden zo min mogelijk gekort. "
        "Gebruik deze pagina als je daar zelf een andere keuze in wilt maken."
    )
    _afdelingen(tabellen)
    _medewerkers(tabellen)
    _regels(tabellen)
    _opruimen(tabellen)
