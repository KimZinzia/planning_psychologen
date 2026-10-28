"""Planning psychologen — webapp (Streamlit).

Starten:  streamlit run app.py

Pagina's:
- Planning:  rooster laten berekenen, bekijken, vergelijken met de huidige
             planning, downloaden en bewaren (ui/planning.py).
- Bijsturen: toewijzingen vastzetten of uitsluiten en opnieuw plannen.
- Invoer:    team, afdelingen, huidige planning en extra eisen (ui/invoer.py).
- Archief:   bewaarde planningen terugkijken en vergelijken (ui/archief.py).

Alle rekenwerk zit in src/ (data.py, model.py, solve.py, controles.py); deze
file en ui/ zijn alleen de gebruikersinterface. De invoer staat in een
SQLite-database (src/store.py).
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import store  # noqa: E402
from data import BIJSTURING_ACTIES, params_from_tables, valideer  # noqa: E402
from ui.archief import pagina_archief  # noqa: E402
from ui.helpers import VERWIJDER, na_editor, namen, uren_kolom, verwijder_kolom, voor_editor  # noqa: E402
from ui.invoer import pagina_invoer  # noqa: E402
import achtergrond  # noqa: E402
from ui.planning import pagina_planning, start_planning, voortgang_zijbalk  # noqa: E402
from ui.stijl import paginakop, pas_stijl_toe, zijbalk_menu, zijbalk_voet  # noqa: E402

st.set_page_config(page_title="Planning psychologen", page_icon=str(Path(__file__).resolve().parent / "assets" / "zinzia_logo.png"), layout="wide")
pas_stijl_toe()

ALLE_LOCATIES = "(alle locaties)"


def pagina_bijsturen() -> None:
    paginakop(
        "Bijsturen",
        "Zet een medewerker <strong>vast</strong> op een afdeling (eventueel met een precies aantal uren), "
        "of <strong>sluit</strong> een medewerker <strong>uit</strong> van een afdeling. Daarna opnieuw plannen.",
    )
    if not store.heeft_gegevens():
        st.info("Er is nog geen invoer.")
        return
    tabellen = store.lees_tabellen()
    E, D, L, _G = namen(tabellen)
    bijsturing = tabellen["Bijsturing"]
    bezig = achtergrond.bezig()
    if bezig:
        st.info("Er wordt een planning berekend. Bijsturing kun je weer aanpassen als die klaar is.", icon=":material/hourglass_top:")

    with st.form("nieuwe_bijsturing", clear_on_submit=True):
        st.markdown("**Nieuwe bijsturing**")
        c1, c2, c3 = st.columns(3)
        medewerker = c1.selectbox("Medewerker", E)
        afdeling = c2.selectbox("Afdeling", D)
        locatie = c3.selectbox("Locatie", [ALLE_LOCATIES, *L], help=f"Bij '{ALLE_LOCATIES}' telt elke locatie van de afdeling mee.")
        c4, c5 = st.columns(2)
        actie = c4.radio("Actie", BIJSTURING_ACTIES, horizontal=True, format_func=str.capitalize)
        uren = c5.number_input("Uren (alleen bij vastzetten; 0 = aantal maakt niet uit)", min_value=0.0, step=0.5)
        if st.form_submit_button("Toevoegen", disabled=bezig):
            loc = None if locatie == ALLE_LOCATIES else locatie
            nieuw = pd.DataFrame(
                [{"medewerker": medewerker, "afdeling": afdeling, "locatie": loc, "actie": actie,
                  "uren": (uren if actie == "vastzetten" and uren > 0 else None)}]
            )
            store.schrijf_tabel("Bijsturing", pd.concat([bijsturing, nieuw], ignore_index=True))
            st.rerun()

    st.markdown("**Huidige bijsturing**")
    if bijsturing.empty:
        st.caption("Nog geen bijsturing.")
    else:
        with st.form("bijsturing_bewerken"):
            st.caption("❌ Vink een regel aan in de eerste kolom en klik op **Wijzigingen opslaan** om hem te verwijderen.")
            bewerkt = st.data_editor(
                voor_editor(bijsturing, ["medewerker", "afdeling"]),
                num_rows="fixed",
                hide_index=True,
                width="stretch",
                height="content",
                column_order=[VERWIJDER, "medewerker", "afdeling", "locatie", "actie", "uren"],
                column_config={
                    VERWIJDER: verwijder_kolom(),
                    "medewerker": st.column_config.SelectboxColumn("Medewerker", options=E, required=True),
                    "afdeling": st.column_config.SelectboxColumn("Afdeling", options=D, required=True),
                    "locatie": st.column_config.SelectboxColumn("Locatie", options=L, help="Leeg = alle locaties"),
                    "actie": st.column_config.SelectboxColumn("Actie", options=BIJSTURING_ACTIES, required=True),
                    "uren": uren_kolom("Uren", help="Leeg = aantal maakt niet uit"),
                },
            )
            if st.form_submit_button("Wijzigingen opslaan", disabled=bezig):
                store.schrijf_tabel("Bijsturing", na_editor(bewerkt))
                st.rerun()

    fouten = [f for f in valideer(params_from_tables(tabellen)) if f.startswith("Bijsturing")]
    for f in fouten:
        st.warning(f)

    st.divider()
    if st.button("Opnieuw plannen", type="primary", disabled=bezig):
        if start_planning():
            st.switch_page(PAGINA_PLANNING)


PAGINA_PLANNING = st.Page(pagina_planning, title="Planning", icon=":material/event_note:", default=True)
PAGINAS = [
    PAGINA_PLANNING,
    st.Page(pagina_bijsturen, title="Bijsturen", icon=":material/tune:", url_path="bijsturen"),
    st.Page(pagina_invoer, title="Invoer", icon=":material/edit_note:", url_path="invoer"),
    st.Page(pagina_archief, title="Archief", icon=":material/inventory_2:", url_path="archief"),
]
# Het standaardmenu van Streamlit verbergen we; de zijbalk bouwen we zelf op
# (logo, menu, voortgang, voet) zodat alles netjes uitgelijnd is.
navigatie = st.navigation(PAGINAS, position="hidden")
with st.sidebar:
    zijbalk_menu(PAGINAS)
    voortgang_zijbalk()
    zijbalk_voet()
navigatie.run()
