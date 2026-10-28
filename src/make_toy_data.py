"""Genereert een klein voorbeeld-Excel-bestand met de structuur die data.py verwacht.

Schrijft naar data/toy_example.xlsx (NIET planning_input.xlsx) zodat dit
script nooit je eigen ingevulde data overschrijft. Handig om opnieuw te
draaien als referentie-check nadat je iets aan het model hebt veranderd:
    .venv\\Scripts\\python.exe src\\make_toy_data.py

De sheets en kolomnamen hier zijn de "contract"-vorm die het dashboard later
moet aanleveren/overschrijven.
"""

import pandas as pd
from pathlib import Path

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "toy_example.xlsx"

employees = pd.DataFrame({
    "medewerker": ["Anna", "Bram", "Carla", "Dries"],
    "gz_psycholoog": [1, 0, 1, 0],
    # in Excel zelf berekend als contracturen min afwezigheden/taken;
    # data.py leest alleen deze kolom, niet hoe hij tot stand komt.
    "Inzetbare tijd": [32, 40, 24, 16],
})

requirements = pd.DataFrame({
    "afdeling": ["Afdeling A", "Afdeling A", "Afdeling B", "Afdeling B"],
    "locatie": ["Locatie 1", "Locatie 2", "Locatie 1", "Locatie 3"],
    # Afdeling B is bewust klein gehouden: max_medewerkers=1 verderop
    # betekent dat 1 medewerker alle uren van Afdeling B in zijn eentje
    # moet dragen, en dat moet passen binnen iemands beschikbare uren.
    "vereiste_uren": [20, 16, 10, 8],
    # 1 = deze uren mogen alleen door een gz-psycholoog worden ingevuld
    "alleen_gz_psycholoog": [0, 0, 1, 0],
    # optioneel: headcount-cap per afdeling (hier: max 1 medewerker op Afdeling B)
    "max_medewerkers": [None, None, None, 1],
    # optioneel: afdelingen met hetzelfde label horen bij elkaar — een
    # medewerker die in de groep werkt, moet dat op 1 locatie doen
    "afdelingsgroep": ["GroepX", "GroepX", None, None],
})

current_schedule = pd.DataFrame({
    "medewerker": ["Anna", "Bram", "Carla", "Dries"],
    "afdeling": ["Afdeling A", "Afdeling A", "Afdeling A", "Afdeling B"],
    "locatie": ["Locatie 1", "Locatie 2", "Locatie 1", "Locatie 3"],
    "huidige_uren": [20, 16, 4, 12],
})

config = pd.DataFrame({
    "key": ["locatie_1", "locatie_2"],
    "value": ["Locatie 1", "Locatie 2"],
})

# optioneel: verplicht minimumaantal uren per medewerker, voor een afdeling
# óf een afdelingsgroep (precies één van de twee invullen per rij). Locatie
# leeg laten betekent "alle locaties van die afdeling/groep tellen mee".
# Uren leeg laten betekent "moet er zijn, aantal maakt niet uit".
minimum_uren = pd.DataFrame({
    "medewerker": ["Anna", "Dries"],
    "afdeling": ["Afdeling A", None],
    "afdelingsgroep": [None, "GroepX"],
    "locatie": ["Locatie 1", None],
    "uren": [5, None],
})

OUTPUT_PATH.parent.mkdir(exist_ok=True)
with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
    employees.to_excel(writer, sheet_name="Employees", index=False)
    requirements.to_excel(writer, sheet_name="Requirements", index=False)
    current_schedule.to_excel(writer, sheet_name="CurrentSchedule", index=False)
    config.to_excel(writer, sheet_name="Config", index=False)
    minimum_uren.to_excel(writer, sheet_name="MinimumUren", index=False)

print(f"Toy data geschreven naar {OUTPUT_PATH}")
