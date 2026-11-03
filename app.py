"""Planning psychologen — webapp (Streamlit).

Starten:  streamlit run app.py

Pagina's:
- Planning:  rooster laten berekenen, bekijken, vergelijken met de huidige
             planning, downloaden en bewaren (ui/planning.py).
- Invoer:    team, afdelingen, huidige planning en extra eisen: afspraken per
             medewerker (minimaal/precies/uitsluiten) en reisregels (ui/invoer.py).
- Noodplanning: tijdelijke aanpassingen met einddatum bij krappe formatie (ui/nood.py).
- Archief:   bewaarde planningen terugkijken en vergelijken (ui/archief.py).
- Toelichting: doelen en regels van het model in gewone taal (ui/toelichting.py).

Alle rekenwerk zit in src/ (data.py, model.py, solve.py, controles.py); deze
file en ui/ zijn alleen de gebruikersinterface. De invoer staat in een
SQLite-database (src/store.py).
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui.archief import pagina_archief  # noqa: E402
from ui.invoer import pagina_invoer  # noqa: E402
from ui.nood import pagina_nood  # noqa: E402
from ui.planning import pagina_planning, voortgang_zijbalk  # noqa: E402
from ui.stijl import pas_stijl_toe, zijbalk_menu, zijbalk_voet  # noqa: E402
from ui.toelichting import pagina_toelichting  # noqa: E402

st.set_page_config(page_title="Planning psychologen", page_icon=str(Path(__file__).resolve().parent / "assets" / "zinzia_logo.png"), layout="wide")
pas_stijl_toe()

PAGINA_PLANNING = st.Page(pagina_planning, title="Planning", icon=":material/event_note:", default=True)
PAGINAS = [
    PAGINA_PLANNING,
    st.Page(pagina_invoer, title="Invoer", icon=":material/edit_note:", url_path="invoer"),
    st.Page(pagina_nood, title="Noodplanning", icon=":material/emergency:", url_path="noodplanning"),
    st.Page(pagina_archief, title="Archief", icon=":material/inventory_2:", url_path="archief"),
    st.Page(pagina_toelichting, title="Toelichting", icon=":material/menu_book:", url_path="toelichting"),
]
# Het standaardmenu van Streamlit verbergen we; de zijbalk bouwen we zelf op
# (logo, menu, voortgang, voet) zodat alles netjes uitgelijnd is.
navigatie = st.navigation(PAGINAS, position="hidden")
with st.sidebar:
    zijbalk_menu(PAGINAS)
    voortgang_zijbalk()
    zijbalk_voet()
navigatie.run()
