"""Kleine hulpfuncties die door meerdere pagina's van de app worden gebruikt."""

import pandas as pd
import streamlit as st

from ui.stijl import STATUS_KLEUREN, legenda  # noqa: F401 - legenda wordt via helpers doorgegeven


def namen(tabellen) -> tuple[list[str], list[str], list[str], list[str]]:
    """Bekende medewerkers, afdelingen, locaties en afdelingsgroepen (voor keuzelijsten)."""
    emp, req, loc = tabellen["Employees"], tabellen["Requirements"], tabellen["Locaties"]
    return (
        sorted(emp["medewerker"].dropna().astype(str).unique()),
        sorted(req["afdeling"].dropna().astype(str).unique()),
        sorted(set(loc["locatie"].dropna().astype(str)) | set(req["locatie"].dropna().astype(str))),
        sorted(req["afdelingsgroep"].dropna().astype(str).unique()),
    )


def naar_bool(df: pd.DataFrame, kolommen: list[str]) -> pd.DataFrame:
    """0/1-kolommen uit de database -> vinkjes in de tabel-editor."""
    df = df.copy()
    for k in kolommen:
        df[k] = pd.to_numeric(df[k], errors="coerce").fillna(0).astype(int).astype(bool)
    return df


def naar_int(df: pd.DataFrame, kolommen: list[str]) -> pd.DataFrame:
    """Vinkjes uit de tabel-editor -> 0/1 voor de database."""
    df = df.copy()
    for k in kolommen:
        df[k] = df[k].fillna(False).astype(bool).astype(int)
    return df


def uren_kolom(label: str | None = None, **kw):
    return st.column_config.NumberColumn(label, min_value=0.0, step=0.5, format="localized", **kw)


RIJ_ID = "_rij"
VERWIJDER = "_verwijder"


def verwijder_kolom():
    """Eerste kolom van elke tabel: aanvinken + Opslaan = rij verwijderen."""
    return st.column_config.CheckboxColumn(
        "❌", help="Verwijderen: vink aan en klik op Opslaan om deze rij te verwijderen.", default=False, width="small", pinned=True
    )


def voor_editor(df: pd.DataFrame, sorteer_op: list[str]) -> pd.DataFrame:
    """Sorteert een tabel voor weergave in de tabel-editor.

    De editor wil een doorlopende index (anders moet de gebruiker bij een
    nieuwe rij zelf een rijnummer invullen). Daarom bewaren we de oorspronkelijke
    index in een verborgen kolom, zodat na het bewerken nog te zien is welke
    rij hernoemd is (zie src/beheer.py). Laat RIJ_ID weg uit column_order.

    Voegt ook de kolom VERWIJDER toe (zet die vooraan in column_order).
    """
    df = df.copy()
    df[RIJ_ID] = df.index
    df.insert(0, VERWIJDER, False)
    return df.sort_values(sorteer_op, key=lambda s: s.astype(str).str.lower()).reset_index(drop=True)


def na_editor(df: pd.DataFrame) -> pd.DataFrame:
    """Haalt de aangevinkte rijen weg en zet de oorspronkelijke index terug;
    nieuwe rijen krijgen een nieuwe index."""
    df = df[~df[VERWIJDER].fillna(False).astype(bool)].drop(columns=VERWIJDER)
    bestaand = pd.to_numeric(df[RIJ_ID], errors="coerce")
    volgende = int(bestaand.max()) + 1 if bestaand.notna().any() else 0
    index = []
    for waarde in bestaand:
        if pd.isna(waarde):
            index.append(volgende)
            volgende += 1
        else:
            index.append(int(waarde))
    return df.drop(columns=RIJ_ID).set_axis(index, axis=0)


# Statusteksten in rooster-tabellen; de kleuren staan in ui/stijl.py.
STATUS_LABELS = {"nieuw": "Nieuw", "gewijzigd": "Gewijzigd", "gestopt": "Gestopt", "gelijk": "Gelijk"}
_LABEL_NAAR_STATUS = {v: k for k, v in STATUS_LABELS.items()}


def kleur_status(df: pd.DataFrame, kolom: str = "Status"):
    """Styler die hele rijen inkleurt op basis van de statuskolom."""
    def rij(r):
        kleur = STATUS_KLEUREN.get(_LABEL_NAAR_STATUS.get(str(r[kolom]), ""))
        stijl = f"background-color: {kleur}; color: #1C1B1A" if kleur and kleur != "#FFFFFF" else ""
        return [stijl] * len(r)
    return df.style.apply(rij, axis=1)


ICONEN = {"fout": ":material/error:", "waarschuwing": ":material/warning:", "info": ":material/info:"}


def toon_signalen(lijst, alleen_belangrijk: bool = False) -> None:
    """Toont aandachtspunten uit controles.signalen.

    alleen_belangrijk: info-meldingen weglaten (bijv. op de planningspagina).
    """
    stijl = {"fout": st.error, "waarschuwing": st.warning, "info": st.info}
    belangrijk = [s for s in lijst if s.niveau != "info"]
    info = [s for s in lijst if s.niveau == "info"]
    for s in belangrijk:
        tekst = s.tekst
        if s.details:
            tekst += "\n\n" + "\n".join(f"- {d}" for d in s.details)
        stijl[s.niveau](tekst, icon=ICONEN[s.niveau])
    if info and not alleen_belangrijk:
        with st.expander(f"Goed om te weten ({len(info)})", icon=ICONEN["info"]):
            for s in info:
                st.markdown(f"**{s.tekst}**")
                if s.details:
                    st.markdown("\n".join(f"- {d}" for d in s.details))


def nl_getal(x) -> str:
    """Getal op z'n Nederlands: 4,5 in plaats van 4.5; leeg blijft leeg."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    return f"{x:g}".replace(".", ",")


def nl_datum(tijdstip: str) -> str:
    """'2026-10-28T14:38:57' -> '28-10-2026 om 14:38'."""
    datum, _, tijd = tijdstip.partition("T")
    jaar, maand, dag = datum.split("-")
    return f"{dag}-{maand}-{jaar} om {tijd[:5]}" if tijd else f"{dag}-{maand}-{jaar}"
