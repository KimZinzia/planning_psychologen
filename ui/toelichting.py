"""Pagina 'Toelichting': de doelen en regels van het planningsmodel in gewone
taal, zodat iedereen kan volgen hoe een planning tot stand komt.

Getallen die in de code vastliggen (zoals de PIOG-productiviteit of de
rekentijd) worden hier uit de code gehaald, en de reisregels uit de invoer.
Zo blijft de uitleg kloppen als er iets verandert.
"""

import streamlit as st

import store
from data import (
    MAX_BEZETTING_BASIS,
    MAX_AFDELINGEN,
    MAX_BEZETTING_NIET_GZ,
    MIN_BEZETTING_NIET_GZ,
    PIOG_PRODUCTIVITEIT,
    STREEF_BEZETTING_GZ,
    params_from_tables,
)
from model import MINIMUM_AANWEZIGHEID
from solve import REIS_GEWICHT, TIJDSLIMIET_PER_STAP
from ui.helpers import nl_getal
from ui.stijl import paginakop
from ui.planning import UITGEZONDERD_HEADCOUNT


def _reisregels() -> tuple[str, str]:
    """(liever niet, nooit) als tekst, uit de huidige invoer."""
    liever, nooit = "twee ingestelde locaties", "geen"
    if store.heeft_gegevens():
        p = params_from_tables(store.lees_tabellen())
        if p.l1 and p.l2:
            liever = f"{p.l1} en {p.l2}"
        if p.nooit_samen:
            nooit = ", ".join(f"{a} en {b}" for a, b in p.nooit_samen)
    return liever, nooit


def _max_basis() -> int:
    if store.heeft_gegevens():
        return round(params_from_tables(store.lees_tabellen()).max_bezetting_basis * 100)
    return round(MAX_BEZETTING_BASIS * 100)


def _werkdruk() -> tuple[int, int, int]:
    if store.heeft_gegevens():
        p = params_from_tables(store.lees_tabellen())
        return round(p.streef_bezetting_gz * 100), round(p.min_bezetting_niet_gz * 100), round(p.max_bezetting_niet_gz * 100)
    return round(STREEF_BEZETTING_GZ * 100), round(MIN_BEZETTING_NIET_GZ * 100), round(MAX_BEZETTING_NIET_GZ * 100)


def _kaart(nummer: int, titel: str, tekst: str, voorbeeld: str) -> None:
    with st.container(border=True, key=f"kaart_toel_doel_{nummer}"):
        st.markdown(f"#### {nummer}. {titel}")
        st.markdown(tekst)
        st.caption(voorbeeld)


def pagina_toelichting() -> None:
    liever, nooit = _reisregels()
    max_basis = _max_basis()
    max_afd = params_from_tables(store.lees_tabellen()).max_afdelingen if store.heeft_gegevens() else MAX_AFDELINGEN
    streef_gz, min_niet_gz, max_niet_gz = _werkdruk()
    piog = round(PIOG_PRODUCTIVITEIT * 100)
    minuten = TIJDSLIMIET_PER_STAP // 60

    paginakop("Toelichting")

    # ------------------------------------------------------------ in het kort
    with st.container():
        st.subheader("In het kort")
        st.markdown(
            "Elke afdeling heeft een aantal uren psycholoog per week nodig, en elke medewerker heeft een aantal "
            "uren beschikbaar. De app zoekt de verdeling van mensen over afdelingen die aan alle regels voldoet "
            "en daarbinnen de doelen zo goed mogelijk haalt.\n\n"
            "Dat zoeken doet een *optimalisatiemodel*: een wiskundige beschrijving van de puzzel, die een "
            "rekenprogramma (de *solver*) oplost. Het probeert niet alle miljoenen mogelijke roosters één voor één, "
            "maar sluit slim hele groepen slechte roosters tegelijk uit."
        )

    # ------------------------------------------------------------ doelen
    st.subheader("De doelen, op volgorde van belang")
    st.markdown(
        "Het eerste doel is het belangrijkst. Zijn er meerdere roosters die daarop even goed scoren, dan kiest "
        "de planning daartussen het rooster dat het beste scoort op het tweede doel, en zo verder. "
        "Een later doel gaat dus nooit ten koste van een eerder doel: de planning maakt bijvoorbeeld nooit een "
        "extra wijziging om op een afdeling minder verschillende mensen te hebben."
    )
    gelijk = "even zwaar als één wijziging" if REIS_GEWICHT == 1 else f"even zwaar als {REIS_GEWICHT} wijzigingen"
    vooraf_links, vooraf_rechts = st.columns(2)
    with vooraf_links.container(border=True, key="kaart_toel_zorg"):
        st.markdown("#### Vooraf: alle afdelingen zorg")
        st.markdown(
            "Elke afdeling krijgt de vereiste uren. Is er te weinig formatie, bijvoorbeeld door verzuim, dan komt er "
            "tóch een rooster: met zo min mogelijk tekort, en dat tekort eerlijk verdeeld. Afdelingen met voorrang "
            "worden zo min mogelijk gekort; de andere ieder ongeveer hetzelfde percentage. De planning laat zien waar "
            "het tekort valt."
        )
        st.caption("Voorrang instellen bij Invoer → Afdelingen. Zelf tijdelijk bijsturen kan op de pagina Noodplanning.")
    with vooraf_rechts.container(border=True, key="kaart_toel_basis"):
        st.markdown("#### Daarna: werkdruk")
        st.markdown(
            f"Een basispsycholoog (geen gz, niet in opleiding) wordt liefst niet voller ingepland dan {max_basis}% van "
            "de inzetbare uren"
            + (f", en niemand werkt liefst op meer dan {max_afd} afdelingen" if max_afd else "")
            + ". Dit gaat vóór de doelen hieronder: alleen als het echt niet anders kan, wijkt de planning ervan af, "
            "en dan zo weinig mogelijk."
        )
        st.caption("In te stellen bij Invoer → Extra eisen → Werkdruk.")
    links, rechts = st.columns(2)
    with links:
        _kaart(
            1, "Zo min mogelijk wijzigingen en reizen",
            "Zo veel mogelijk blijft zoals het nu is (de huidige planning), en zo weinig mogelijk mensen werken op "
            f"{liever} allebei. Die twee zijn even belangrijk: één reiziger telt {gelijk}. Een wijziging is één "
            "medewerker op één afdeling op één locatie die nieuw is, stopt of een ander aantal uren krijgt.",
            "Voorbeeld: kost het 3 wijzigingen om één reiziger te voorkomen, dan blijft die reiziger (1 telt minder "
            "dan 3). Kost het 1 wijziging, dan maakt het niet uit.",
        )
        _kaart(
            3, "De uren eerlijk verdelen",
            f"Wie geen gz-psycholoog is, wordt liefst tussen {min_niet_gz}% en {max_niet_gz}% van de inzetbare uren "
            "ingepland. "
            f"Gz-psychologen zitten zo dicht mogelijk bij {streef_gz}%. Daarna zijn binnen elke groep de uren zo "
            "gelijk mogelijk verdeeld (bezetting = ingeplande uren gedeeld door inzetbare uren). Dit zijn voorkeuren, "
            "geen harde grenzen. Er worden geen uren bijgemaakt, alleen verschoven, en alles in hele en halve uren.",
            "In te stellen bij Invoer → Extra eisen → Werkdruk.",
        )
    with rechts:
        _kaart(
            2, "Liefst een gz-psycholoog bij alle IDO's",
            "Geldt voor afdelingen met meerdere IDO's. Elke IDO heeft sowieso een psycholoog "
            "(dat is een regel), maar het liefst is er een gz-psycholoog die bij alle IDO's van de afdeling is.",
            "Voorbeeld: Rijnhof heeft IDO's op dinsdag en donderdag; liefst werkt er een gz-psycholoog die bij "
            "allebei kan zijn.",
        )
        _kaart(
            4, "Zo weinig mogelijk verschillende mensen per afdeling",
            "Een afdeling heeft liever een paar vaste gezichten dan veel mensen met elk een paar uur. Geteld wordt "
            f"per afdeling het aantal verschillende medewerkers, opgeteld over alle afdelingen. "
            f"{UITGEZONDERD_HEADCOUNT} telt hier niet mee: daar zijn meerdere collega's juist prima.",
            "Voorbeeld: liever één psycholoog 16 uur op een afdeling dan twee psychologen elk 8 uur.",
        )
    with st.container(border=True, key="kaart_toel_reizen"):
        st.markdown("#### Reizen: nooit en liever niet")
        st.markdown(
            f"- {nooit}: nooit. Dit is een harde regel; geen enkele planning doet het.\n"
            f"- {liever}: liever niet, alleen als het echt niet anders kan (telt mee in doel 1)."
        )
        st.caption("In te stellen bij Invoer → Extra eisen → Reizen beperken.")

    # ------------------------------------------------------------ regels
    st.subheader("De regels die altijd gelden")
    st.markdown(
        "Dit zijn harde eisen: een planning die er één overtreedt, wordt nooit gekozen, hoe goed hij verder ook is. "
        "Kan er geen planning aan alle regels voldoen, dan meldt de app welke regels botsen."
    )
    regels = [
        ("Genoeg uren per afdeling", "Elke afdeling krijgt minstens de vereiste uren per week. Alleen bij te weinig "
         "formatie kan dat niet; dan verdeelt de planning het tekort (zie 'Vooraf' hierboven).", "Invoer → Afdelingen"),
        ("Minimaal 1 uur", f"Wie op een afdeling werkt, werkt daar minstens {nl_getal(MINIMUM_AANWEZIGHEID)} uur per week.",
         "Vast in het model"),
        ("Niet meer dan inzetbaar", "Niemand wordt meer uren ingepland dan die inzetbaar is (zie hieronder).", "Invoer → Team"),
        ("Gz-psycholoog per locatie", "Op elke locatie werkt minstens één gz-psycholoog.", "Invoer → Team (kolom gz)"),
        ("Alleen gz", "Sommige afdelingen alleen voor gz-psychologen. Uitzondering: een afspraak zet iemand er "
         "toch neer; dan werkt er ook een gz-psycholoog naast.", "Invoer → Afdelingen (Alleen gz)"),
        ("Maximum per afdeling", "Niet meer verschillende medewerkers op een afdeling dan het ingestelde maximum.",
         "Invoer → Afdelingen (Max. medewerkers)"),
        ("Afdelingsgroep op één locatie", "Afdelingen met hetzelfde label (bv. GRZ) doet een medewerker op één locatie.",
         "Invoer → Afdelingen (Afdelingsgroep)"),
        ("IDO", "Bij elke IDO is een psycholoog van de afdeling, en wie op een afdeling werkt, is bij minstens één "
         "IDO van die afdeling (en werkt dus op die dag). Werkt er maar één psycholoog, dan is die bij alle IDO's. "
         "Uitzondering: een afspraak 'Hoeft niet bij IDO'.",
         "Invoer → Afdelingen (IDO's) en Team (werkdagen)"),
        ("Niet twee IDO's tegelijk", "Niemand hoeft bij twee IDO's op hetzelfde moment te zijn: zelfde dag, zelfde "
         "weken (elke week botst met even én oneven) en overlappende tijden. Een gezamenlijke IDO van twee "
         "afdelingen (zelfde label bij 'Samen') telt als één.",
         "Invoer → Afdelingen (IDO's)"),
        ("Nooit combineren", f"Niemand werkt op twee locaties die nooit samen mogen (nu: {nooit}).",
         "Invoer → Extra eisen → Reizen beperken"),
        ("Afspraken", "Per medewerker: minstens of precies een aantal uren op een afdeling, groep of locatie, of daar "
         f"juist niet. 'Moet er werken' zonder aantal betekent minstens {nl_getal(MINIMUM_AANWEZIGHEID)} uur.",
         "Invoer → Extra eisen → Afspraken"),
    ]
    with st.container(border=True, key="kaart_toel_regels"):
        tabel = "| Regel | Wat het betekent | Waar stel je het in |\n|---|---|---|\n" + "\n".join(
            f"| **{r}** | {w} | {i} |" for r, w, i in regels
        )
        st.markdown(tabel)

    # ------------------------------------------------------------ inzetbare uren
    with st.container(border=True, key="kaart_toel_uren"):
        st.subheader("Hoe worden de inzetbare uren berekend?")
        st.markdown(
            "1. Begin bij de contracturen.\n"
            "2. Trek de aftrekposten eraf: lesuren en opleidingsuren PIOG, externe opleiding, ouderschapsverlof, "
            "opleidingstijd, coördinerende taken en overig.\n"
            f"3. Is iemand in opleiding (PIOG), dan telt de rest voor {piog}%: wie in opleiding is, heeft meer "
            "tijd nodig voor hetzelfde werk, dus worden er minder uren ingepland.\n"
            "4. Bij zwangerschapsverlof is het 0."
        )
        st.caption(
            "Voorbeeld: 28 contracturen − 4 lesuren − 6 opleidingsuren = 18 uur. Deze medewerker is PIOG, dus "
            f"{piog}% van 18 = {nl_getal(18 * PIOG_PRODUCTIVITEIT)} uur inzetbaar."
        )

    # ------------------------------------------------------------ rekenen
    links, rechts = st.columns(2)
    with links.container(border=True, key="kaart_toel_rekenen"):
        st.subheader("Hoe lang duurt het rekenen?")
        st.markdown(
            f"Elk doel is één rekenstap van maximaal {minuten} minuten. Meestal is een stap veel sneller klaar. "
            "Je kunt intussen gewoon verder werken; de voortgang staat in de zijbalk.\n\n"
            "Is de tijd op, dan gaat de app verder met het beste rooster dat tot dan gevonden is. Dat rooster voldoet "
            "aan alle regels, maar het is dan niet bewezen dat er geen nóg iets beter rooster bestaat. "
            "De app meldt dat bij het resultaat."
        )
    with rechts.container(border=True, key="kaart_toel_niet"):
        st.subheader("Wat neemt de planning niet mee?")
        st.markdown(
            "- De planning is per week, niet per dag. Werkdagen en tijden tellen alleen mee voor de IDO's.\n"
            "- Voorkeuren van medewerkers, behalve wat je als afspraak invult.\n"
            "- Reistijd tussen andere locaties dan de ingestelde reisregels.\n"
            "- De planning is een voorstel: controleer het altijd, en stuur bij met afspraken als iets niet klopt."
        )

    # ------------------------------------------------------------ formules
    with st.expander("Voor de liefhebber: het wiskundige model", icon=":material/function:"):
        st.markdown(
            "**Beslissingen.** $x_{e,d,l} \\ge 0$: uren van medewerker $e$ op afdeling $d$ op locatie $l$. "
            "Daarnaast ja/nee-variabelen: werkt $e$ op afdeling $d$ ($y_{e,d}$), op locatie $l$ ($y^{loc}_{e,l}$), "
            "wijkt een toewijzing af van nu ($z_{e,d,l}$), reist $e$ ($w_e$), is $e$ bij IDO $k$ ($a_{e,k}$), mist afdeling $d$ een gz-psycholoog bij alle IDO's ($g_{d,l}$)."
        )
        st.markdown("**Belangrijkste regels.**")
        st.latex(r"\sum_e x_{e,d,l} \ge \text{vereist}_{d,l} \qquad \sum_{d,l} x_{e,d,l} \le \text{inzetbaar}_e")
        st.latex(r"\sum_{e \in \text{gz}} y^{loc}_{e,l} \ge 1 \qquad y^{loc}_{e,a} + y^{loc}_{e,b} \le 1 \;\; \text{(nooit combineren)}")
        st.latex(r"a_{e,k} + a_{e,k'} \le 1 \;\; \text{(IDO's } k, k' \text{ tegelijk)} \qquad \sum_e a_{e,k} \ge 1")
        st.markdown(
            "**Doelen**, lexicografisch: elk doel wordt geminimaliseerd terwijl de uitkomst van de vorige doelen "
            "wordt vastgehouden."
        )
        st.latex(
            r"\min \Big(\sum z_{e,d,l} + \sum_e w_e\Big) \;\;\succ\;\; \min \sum_{d,l} g_{d,l} \;\;\succ\;\; \min\Big(\max_e b_e - \min_e b_e\Big)"
            r"\;\;\succ\;\; \min \sum_{e,\, d \ne \text{AGT}} y_{e,d}, \quad b_e = \tfrac{100}{\text{inzetbaar}_e}\sum_{d,l} x_{e,d,l}"
        )
        st.caption(
            "Opgelost met PuLP (modelleertaal) en HiGHS (solver), beide open source. De code staat in src/model.py "
            "en src/solve.py; elke functie daar hoort bij één regel of doel hierboven."
        )
