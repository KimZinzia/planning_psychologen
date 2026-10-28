"""Huisstijl van de app (Zinzia): logo, paginakop, legenda en een klein beetje
extra CSS bovenop het thema in .streamlit/config.toml.

De meeste opmaak (kleuren, lettertype, afrondingen) staat in config.toml.
Hier staat alleen wat het thema niet kan. CSS richt zich zoveel mogelijk op
klassen die we zelf bepalen (containers met een key beginnend met 'kaart'),
zodat een Streamlit-update de opmaak niet ongemerkt breekt.
"""

import base64
from html import escape
from pathlib import Path

import streamlit as st

LOGO = Path(__file__).resolve().parent.parent / "assets" / "zinzia_logo.png"

GOUD = "#B38C4F"
GOUD_DONKER = "#8A6A36"
CREME = "#F7F4ED"
RAND = "#E7E1D6"
TEKST_ZACHT = "#6B655C"

# Hoogtes (rem) waarmee de lijn onder de paginatitel en de lijn onder het
# logo in de zijbalk op dezelfde hoogte vallen: ZIJBALK_KOP + MERK_HOOGTE
# + CORRECTIE = LIJN_HOOGTE. CORRECTIE compenseert de vaste ruimte (1rem) die
# Streamlit boven de hoofdinhoud zet (gemeten met screenshots).
LIJN_HOOGTE = 12.0
ZIJBALK_KOP = 2.6
CORRECTIE = -1.0
MERK_HOOGTE = LIJN_HOOGTE - ZIJBALK_KOP - CORRECTIE

# Achtergrondkleuren voor de status van een toewijzing (tinten uit het logo).
STATUS_KLEUREN = {
    "nieuw": "#DDF1EF",      # turquoise
    "gewijzigd": "#F6EAD2",  # zand/goud
    "gestopt": "#F9DCE7",    # roze
    "gelijk": "#FFFFFF",
}
STATUS_UITLEG = {
    "nieuw": "Nieuw",
    "gewijzigd": "Aantal uren gewijzigd",
    "gestopt": "Gestopt",
    "gelijk": "Blijft gelijk",
}

_CSS = f"""
<style>
/* Paginakop */
/* Paginakop en logovak hebben een vaste hoogte, zodat de lijn onder de
   titel precies gelijk ligt met de lijn onder het logo in de zijbalk.
   Alles in rem, zodat het ook bij in- en uitzoomen gelijk blijft. */
[data-testid="stMainBlockContainer"] {{ padding-top: 0 !important; }}
.zz-kop {{ box-sizing: border-box; height: {LIJN_HOOGTE}rem; margin: 0; padding-bottom: 1.1rem;
          display: flex; flex-direction: column; justify-content: flex-end; border-bottom: 1px solid {RAND}; }}
.zz-kop .zz-boven {{ color: {GOUD_DONKER}; font-size: 0.78rem; font-weight: 600; letter-spacing: 0.14em;
                     text-transform: uppercase; margin-bottom: 0.15rem; }}
.zz-kop h1 {{ margin: 0; padding: 0; line-height: 1.15; }}
.zz-onder {{ color: {TEKST_ZACHT}; margin: 1rem 0 1.4rem 0; font-size: 1rem; line-height: 1.5; }}

/* Kaarten (containers met key 'kaart…') */
[class*="st-key-kaart"] {{ background: #FFFFFF; box-shadow: 0 1px 2px rgba(28, 27, 26, 0.04),
                           0 4px 14px rgba(28, 27, 26, 0.05); }}

/* Legenda */
.zz-legenda {{ display: flex; flex-wrap: wrap; gap: 0.5rem 1.1rem; margin: 0.2rem 0 0.6rem 0;
              font-size: 0.85rem; color: {TEKST_ZACHT}; }}
.zz-legenda span {{ display: inline-flex; align-items: center; gap: 0.4rem; }}
.zz-legenda i {{ width: 0.95rem; height: 0.95rem; border-radius: 0.25rem; border: 1px solid {RAND};
                display: inline-block; }}

/* Zijbalk: eigen indeling (logo, menu, voet). De kopregel bevat alleen het
   inklap-pijltje en mag dus laag blijven. */
[data-testid="stSidebarHeader"] {{ box-sizing: border-box; height: {ZIJBALK_KOP}rem; min-height: {ZIJBALK_KOP}rem;
                                    padding: 0.6rem 0.9rem 0 0; margin: 0; }}
[data-testid="stSidebarUserContent"] {{ padding-top: 0; }}
.zz-merk {{ box-sizing: border-box; height: {MERK_HOOGTE}rem; display: flex; flex-direction: column;
           justify-content: flex-end; align-items: center; text-align: center; padding: 0 0 1.1rem 0; margin-bottom: 1.6rem;
           border-bottom: 1px solid {RAND}; }}
.zz-merk img {{ width: 9rem; max-width: 80%; height: auto; display: block; margin: 0 auto; }}
.zz-merk .zz-app {{ margin-top: 1rem; color: {TEKST_ZACHT}; font-size: 0.8rem; font-weight: 600;
                   letter-spacing: 0.14em; text-transform: uppercase; }}
.zz-menu-kop {{ color: {TEKST_ZACHT}; font-size: 0.72rem; font-weight: 600; letter-spacing: 0.14em;
               text-transform: uppercase; margin: 0 0 0.5rem 0.75rem; }}
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] {{ padding: 0.4rem 0.75rem; border-radius: 0.6rem; }}
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] p {{ font-size: 0.97rem; }}
.zz-scheiding {{ border-top: 1px solid {RAND}; margin: 1.6rem 0 1rem 0; }}

/* Tabbladen wat steviger. */
[data-testid="stTabs"] button p {{ font-weight: 600; }}

/* Kleine voettekst in de zijbalk */
.zz-voet {{ color: {TEKST_ZACHT}; font-size: 0.75rem; line-height: 1.5; text-align: center; }}
</style>
"""


def pas_stijl_toe() -> None:
    """Eén keer per paginaload aanroepen (in app.py)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def _logo_base64() -> str:
    return base64.b64encode(LOGO.read_bytes()).decode()


def zijbalk_menu(paginas) -> None:
    """Logo (groot, gecentreerd) en het menu bovenaan de zijbalk."""
    st.markdown(
        f'<div class="zz-merk"><img src="data:image/png;base64,{_logo_base64()}" alt="Zinzia Zorggroep">'
        '<div class="zz-app">Planning psychologen</div></div>'
        '<div class="zz-menu-kop">Menu</div>',
        unsafe_allow_html=True,
    )
    for pagina in paginas:
        st.page_link(pagina, width="stretch")


def paginakop(titel: str, ondertitel: str | None = None) -> None:
    """Kop bovenaan elke pagina, in de stijl van zinzia.nl."""
    onder = f'<div class="zz-onder">{ondertitel}</div>' if ondertitel else '<div class="zz-onder"></div>'
    st.markdown(
        f'<div class="zz-kop"><div class="zz-boven">Zinzia · Planning psychologen</div>'
        f"<h1>{escape(titel)}</h1></div>{onder}",
        unsafe_allow_html=True,
    )


def legenda() -> None:
    """Legenda voor gekleurde rooster-tabellen; zet hem bóven de tabel."""
    delen = "".join(
        f'<span><i style="background:{STATUS_KLEUREN[s]}"></i>{STATUS_UITLEG[s]}</span>' for s in STATUS_KLEUREN
    )
    st.markdown(f'<div class="zz-legenda">{delen}</div>', unsafe_allow_html=True)


def zijbalk_voet() -> None:
    st.markdown(
        '<div class="zz-scheiding"></div><div class="zz-voet">Zinzia Zorggroep · intern gebruik</div>',
        unsafe_allow_html=True,
    )
