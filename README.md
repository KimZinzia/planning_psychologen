# Planning psychologen

Webapp die de weekplanning van psychologen over afdelingen en locaties maakt.
Een optimalisatiemodel verdeelt de vereiste uren over de medewerkers en houdt
daarbij rekening met (op volgorde):

0. **masterpsychologen** (geen gz, niet in opleiding) liefst niet voller dan een
   in te stellen percentage (standaard 85%) van hun inzetbare uren, en niemand
   liefst op meer dan een in te stellen aantal afdelingen (standaard 5);
1. **zo min mogelijk wijzigingen** ten opzichte van de huidige planning én
   **zo weinig mogelijk reizen** tussen twee ingestelde locaties ("liever niet
   combineren", bv. ONO en LH); die twee tellen even zwaar;
2. daarbinnen bij afdelingen met meerdere IDO's **liefst een
   gz-psycholoog bij alle IDO's**;
3. daarbinnen de uren **eerlijk verdelen**: masterpsychologen liefst tussen
   75% en 85%, gz-psychologen zo dicht mogelijk bij 90% en PIOG'ers bij 100%
   van hun inzetbare uren (alle in te stellen), en binnen elke groep iedereen
   ongeveer even vol, in hele of halve uren;
4. daarbinnen **zo weinig mogelijk medewerkers per afdeling**.

Harde regels: vereiste uren per afdeling/locatie worden gedekt, niemand werkt
meer dan de inzetbare tijd, minimaal één gz-psycholoog per locatie, sommige
afdelingen alleen voor gz-psychologen, en optioneel: maximum aantal
medewerkers per afdeling, verplichte
IDO's (dag, even/oneven weken en tijd; niemand bij twee IDO's tegelijk), locaties die nooit gecombineerd mogen worden (bv. LH en RK), locaties die altijd samen gaan (wie op Rijnhof werkt, werkt ook op ONO) en
afspraken per medewerker (minimaal/precies X uur, of uitsluiten; voor een
afdeling, een afdelingsgroep of een hele locatie).

Alle gebruikte software is open source (geen licentiekosten):
[PuLP](https://github.com/coin-or/pulp) als modelleertaal,
[HiGHS](https://highs.dev) als solver en [Streamlit](https://streamlit.io) voor
de webinterface.

## De app gebruiken

| Pagina | Wat je er doet |
|---|---|
| **Invoer** | Team (contracturen, aftrekposten, werkdagen), afdelingen (met IDO's), huidige planning en extra eisen (afspraken per medewerker en reisregels) bijhouden. Medewerkers voer je op één plek in; elders kies je ze uit een lijst. Hernoemen of verwijderen werkt automatisch door in de andere tabellen. |
| **Planning** | Op *Plan maken* klikken (duurt 5 à 10 minuten), het rooster bekijken, de wijzigingen ten opzichte van de huidige planning zien en het resultaat als Excel downloaden. Een goedgekeurd rooster kan worden overgenomen als nieuwe huidige planning. |
| **Noodplanning** | Tijdelijke aanpassingen met einddatum bij krappe formatie: andere uren per afdeling, meer/minder uren per medewerker, regels tijdelijk loslaten. Bij te weinig formatie maakt de planning ook zonder aanpassingen een rooster en verdeelt het tekort eerlijk (afdelingen met voorrang zo min mogelijk). |
| **Archief** | Bewaarde planningen (bijv. per periode) terugkijken en met elkaar of met de huidige planning vergelijken. |
| **Toelichting** | De doelen en regels van het model in gewone taal, met voorbeelden (en de formules voor wie dat wil). |

Bovenaan Invoer en Planning staan automatische **aandachtspunten**, zoals te
weinig ruimte, of een afdeling waar op de IDO-dag niemand kan zijn. Onderaan
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
app.py               webinterface: menu en zijbalk
ui/planning.py       webinterface: pagina Planning (en roosterweergave)
ui/invoer.py         webinterface: pagina Invoer
ui/nood.py           webinterface: pagina Noodplanning
ui/archief.py        webinterface: pagina Archief
ui/toelichting.py    webinterface: pagina Toelichting (model in gewone taal)
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
  afdeling/locatie-combinaties 5 à 10 minuten (hooguit 5 minuten per rekenstap) rekentijd op één CPU-kern en
  weinig geheugen (< 500 MB).
- **Instellingen:** het pad van de database is in te stellen met de
  omgevingsvariabele `PLANNING_DB` (standaard `data/planning.db`).
