"""Pagina 'Archief': bewaarde planningen terugkijken, vergelijken en verwijderen."""

import pandas as pd
import streamlit as st

import store
from data import params_from_tables
from output import excel_bytes
from solve import huidig_rooster
from ui.planning import toon_kerncijfers, toon_rooster
from ui.stijl import paginakop

HUIDIG = "De huidige planning (invoer)"
LAATST = "De laatst berekende planning"


def _datum(tijdstip: str) -> str:
    d, _, t = tijdstip.partition("T")
    jaar, maand, dag = d.split("-")
    return f"{dag}-{maand}-{jaar} {t[:5]}"


def pagina_archief() -> None:
    paginakop(
        "Archief",
        "Hier staan de planningen die je hebt bewaard (via <strong>Bewaren in archief</strong> op de pagina Planning).<br>"
        "Bekijk een eerdere planning of vergelijk twee planningen met elkaar.",
    )
    lijst = store.archief_lijst()
    if lijst.empty:
        st.info("Er is nog niets bewaard. Maak een planning en klik op **Bewaren in archief**.")
        return

    overzicht = pd.DataFrame({
        "Naam": lijst["naam"],
        "Bewaard op": lijst["tijdstip"].map(_datum),
        "Gewijzigde toewijzingen": lijst["kpi"].map(lambda k: k.get("deviation")),
        "Op beide reislocaties": lijst["kpi"].map(lambda k: k.get("travel")),
        "Koppels (excl. AGT)": lijst["kpi"].map(lambda k: k.get("headcount")),
    })
    with st.container(border=True):
        st.subheader("Bewaarde planningen")
        st.dataframe(overzicht, hide_index=True, width="stretch", height="content")

    opties = dict(zip(lijst["id"], lijst["naam"] + "  (" + lijst["tijdstip"].map(_datum) + ")"))
    c1, c2 = st.columns(2)
    gekozen = c1.selectbox("Bekijk planning", list(opties), format_func=opties.get)
    andere = [i for i in opties if i != gekozen]
    vergelijk = c2.selectbox(
        "Vergelijk met", [HUIDIG, LAATST, *andere],
        format_func=lambda o: o if isinstance(o, str) else opties[o],
    )

    tabellen = store.lees_tabellen()
    p = params_from_tables(tabellen)
    rooster = store.archief_rooster(gekozen)
    if vergelijk == HUIDIG:
        oud, label = huidig_rooster(p), "Nu"
    elif vergelijk == LAATST:
        resultaat = store.lees_resultaat()
        if resultaat is None:
            st.warning("Er is nog geen berekende planning; er wordt vergeleken met de huidige planning.")
            oud, label = huidig_rooster(p), "Nu"
        else:
            oud, label = resultaat[0], "Laatst berekend"
    else:
        oud, label = store.archief_rooster(vergelijk), opties[vergelijk].split("  (")[0]

    st.divider()
    st.subheader(opties[gekozen].split("  (")[0])
    st.caption(
        "De vereiste uren, werkdagen en inzetbare tijd hieronder komen uit de invoer zoals die nu is, "
        "niet uit het moment waarop de planning is bewaard."
    )
    toon_kerncijfers(p, rooster, oud, sleutel="archief", label_oud=label)
    export = toon_rooster(p, rooster, oud, sleutel="archief", label_oud=label, label_nieuw="Deze planning")

    st.divider()
    a1, a2 = st.columns(2)
    a1.download_button(
        "Download als Excel", icon=":material/download:", data=excel_bytes(export),
        file_name=f"{opties[gekozen].split('  (')[0]}.xlsx".replace("/", "-"),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch",
    )
    with a2.popover("Verwijderen uit archief", icon=":material/delete:", width="stretch"):
        st.markdown(f"Weet je zeker dat je **{opties[gekozen]}** wilt verwijderen? Dit kan niet ongedaan worden gemaakt.")
        if st.button("Ja, verwijderen", type="primary"):
            store.archief_verwijder(gekozen)
            st.rerun()
