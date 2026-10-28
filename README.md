# Planning psychologen

Webapp die de weekplanning van psychologen over afdelingen en locaties maakt.
Een optimalisatiemodel verdeelt de vereiste uren over de medewerkers en houdt
daarbij rekening met:

1. **zo min mogelijk wijzigingen** ten opzichte van de huidige planning;
2. daarbinnen **zo weinig mogelijk reizen** tussen twee ingestelde locaties;
3. daarbinnen **zo weinig mogelijk medewerkers per afdeling**.

Harde regels: vereiste uren per afdeling/locatie worden gedekt, niemand werkt
meer dan de inzetbare tijd, minimaal één gz-psycholoog per locatie, sommige
afdelingen alleen voor gz-psychologen, en optioneel: maximum aantal
medewerkers per afdeling, afdelingsgroepen op één locatie, verplichte
minimumuren, en handmatige bijsturing (vastzetten/uitsluiten).

Alle gebruikte software is open source (geen licentiekosten):
[PuLP](https://github.com/coin-or/pulp) als modelleertaal,
[HiGHS](https://highs.dev) als solver en [Streamlit](https://streamlit.io) voor
de webinterface.

## De app gebruiken

| Pagina | Wat je er doet |
|---|---|
| **Invoer** | Team (contracturen, aftrekposten, werkdagen), afdelingen (met MDO-dag), huidige planning en extra eisen (minimumuren) bijhouden. Medewerkers voer je op één plek in; elders kies je ze uit een lijst. Hernoemen of verwijderen werkt automatisch door in de andere tabellen. |
| **Planning** | Op *Plan maken* klikken (duurt enkele minuten), het rooster bekijken, de wijzigingen ten opzichte van de huidige planning zien en het resultaat als Excel downloaden. Een goedgekeurd rooster kan worden overgenomen als nieuwe huidige planning. |
| **Bijsturen** | Een medewerker vastzetten op of uitsluiten van een afdeling en opnieuw plannen. |
| **Archief** | Bewaarde planningen (bijv. per periode) terugkijken en met elkaar of met de huidige planning vergelijken. |

Bovenaan Invoer en Planning staan automatische **aandachtspunten**, zoals te
weinig ruimte, of een afdeling waar op de MDO-dag niemand kan zijn. Onderaan
Invoer staat een **back-up**: alle invoer downloaden als Excel en terugzetten.

Is er geen planning mogelijk, dan laat de app zien welke groep regels het
knelpunt vormt (bijvoorbeeld "Inzetbare tijd per medewerker").

## Lokaal starten

Vereist Python 3.12 of nieuwer.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

De app opent op http://localhost:8501.

## Projectstructuur

```
app.py               webinterface: menu en pagina Bijsturen
ui/planning.py       webinterface: pagina Planning (en roosterweergave)
ui/invoer.py         webinterface: pagina Invoer
ui/archief.py        webinterface: pagina Archief
ui/helpers.py        gedeelde hulpfuncties voor de webinterface
ui/stijl.py          huisstijl Zinzia: logo, paginakop, legenda
.streamlit/config.toml  thema (kleuren, lettertype)
assets/              logo
src/beheer.py        houdt tabellen consistent bij hernoemen/verwijderen
src/controles.py     aandachtspunten in de invoer (vóór het plannen)
src/data.py          invoertabellen -> Params; Excel inlezen; validatie
src/model.py         optimalisatiemodel (variabelen en constraints)
src/solve.py         lexicografisch oplossen met HiGHS, vergelijkingen
src/store.py         opslag van invoer en resultaat (SQLite)
src/output.py        export naar Excel
planning_v1.ipynb    notebook om het model los van de app te draaien
```

## In productie zetten (voor IT)

De app is een Python-webserver (Streamlit) die op poort 8501 luistert.

**Draaien met Docker**

```bash
docker build -t planning-psychologen .
docker run -d -p 8501:8501 -v planning-data:/app/data planning-psychologen
```

De volume `planning-data` bevat de database (`planning.db`). Maak daar
regelmatig een back-up van.

**Aandachtspunten**

- **Inloggen:** Streamlit heeft zelf geen gebruikersbeheer. Zet de app achter
  de bestaande single sign-on, bijvoorbeeld een reverse proxy met Microsoft
  Entra ID (Azure AD), of Azure App Service met ingebouwde authenticatie.
- **Privacy:** de database bevat namen en verlofgegevens van medewerkers
  (persoonsgegevens, AVG). Alleen toegankelijk maken voor planners, en de
  database niet in git zetten (`data/` staat in `.gitignore`).
- **Meerdere gebruikers:** de opslag is één SQLite-bestand en gaat uit van één
  planner tegelijk. Gaan meerdere planners tegelijk werken, dan is een
  gedeelde database (bijv. PostgreSQL) de logische volgende stap. Alleen
  `src/store.py` hoeft dan te veranderen.
- **Rekentijd en geheugen:** een planning kost bij ~15 medewerkers en ~35
  afdeling/locatie-combinaties enkele minuten (maximaal ~6) rekentijd op één CPU-kern en
  weinig geheugen (< 500 MB).
- **Instellingen:** het pad van de database is in te stellen met de
  omgevingsvariabele `PLANNING_DB` (standaard `data/planning.db`).
