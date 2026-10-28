"""Schrijft de resultaten (rooster + overzichten) naar één Excel-bestand met
een tabblad per tabel, zodat je het resultaat los van Python kan bekijken/delen.
"""

from io import BytesIO
from pathlib import Path

import pandas as pd


def write_results(
    path: str | Path,
    schedule: pd.DataFrame,
    per_medewerker: pd.DataFrame,
    afdeling_verschil: pd.DataFrame,
    medewerker_verschil: pd.DataFrame,
    travel_pair: pd.DataFrame,
) -> None:
    path = Path(path)
    path.parent.mkdir(exist_ok=True)

    # locaties/afdelingen staan als Python-lijsten; Excel-cellen kunnen dat
    # niet tonen, dus we zetten ze om naar leesbare, kommagescheiden tekst.
    per_medewerker = per_medewerker.copy()
    per_medewerker["locaties"] = per_medewerker["locaties"].apply(", ".join)
    per_medewerker["afdelingen"] = per_medewerker["afdelingen"].apply(", ".join)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        schedule.to_excel(writer, sheet_name="Rooster", index=False)
        per_medewerker.to_excel(writer, sheet_name="Per medewerker", index=False)
        afdeling_verschil.to_excel(writer, sheet_name="Verschil per afdeling", index=False)
        medewerker_verschil.to_excel(writer, sheet_name="Verschil per medewerker", index=False)
        travel_pair.to_excel(writer, sheet_name="Reispaar", index=False)


def excel_bytes(tabbladen: dict[str, pd.DataFrame]) -> bytes:
    """Zet een aantal tabellen om naar een Excel-bestand in het geheugen
    (één tabblad per tabel), bv. voor een downloadknop in de app."""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for naam, df in tabbladen.items():
            df.to_excel(writer, sheet_name=naam[:31], index=False)  # Excel: max 31 tekens
    return buffer.getvalue()
