"""Houdt de invoertabellen onderling consistent bij wijzigingen vanuit de app.

Medewerkers, locaties en afdelingen worden elk op één plek ingevoerd
(Employees, Locaties, Requirements); de andere tabellen verwijzen ernaar.
Wordt zo'n naam hernoemd of verwijderd, dan passen de functies hieronder de
verwijzingen mee aan, zodat er nooit regels achterblijven met een naam die
niet meer bestaat.

Hernoemen wordt herkend aan het rijnummer (de index): de tabel-editor in de
app behoudt de index van bestaande rijen en geeft nieuwe rijen een nieuwe
index. Een rij met dezelfde index maar een andere naam is dus hernoemd.

Elke functie krijgt de huidige tabellen en de bewerkte tabel, en geeft
(nieuwe tabellen, meldingen, fouten) terug. Bij fouten wordt niets opgeslagen.
"""

import pandas as pd

Tabellen = dict[str, pd.DataFrame]


def _naam(w) -> str | None:
    if w is None or (isinstance(w, float) and pd.isna(w)):
        return None
    w = str(w).strip()
    return w or None


def _hernoemingen(oud: pd.DataFrame, nieuw: pd.DataFrame, kolom: str) -> dict[str, str]:
    """{oude naam: nieuwe naam} voor rijen die bleven bestaan maar een andere naam kregen."""
    result = {}
    for i in oud.index.intersection(nieuw.index):
        o, n = _naam(oud.at[i, kolom]), _naam(nieuw.at[i, kolom])
        if o and n and o != n:
            result[o] = n
    return result


def _dubbele(namen: pd.Series) -> list[str]:
    namen = namen.map(_naam).dropna()
    return sorted(namen[namen.duplicated()].unique())


def _meervoud(n: int, enkel: str, meer: str) -> str:
    return f"{n} {enkel if n == 1 else meer}"


def vul_locaties_aan(t: Tabellen) -> tuple[Tabellen, bool]:
    """Zorgt dat elke locatie die ergens gebruikt wordt in de lijst Locaties staat
    (nodig bij een database van vóór de lijst Locaties bestond)."""
    bekend = [l for l in t["Locaties"]["locatie"].map(_naam).dropna()]
    gebruikt = list(t["Requirements"]["locatie"].map(_naam).dropna()) + list(t["Config"]["value"].map(_naam).dropna())
    ontbrekend = sorted(set(gebruikt) - set(bekend))
    if not ontbrekend:
        return t, False
    t = dict(t)
    t["Locaties"] = pd.DataFrame({"locatie": sorted(set(bekend) | set(ontbrekend))})
    return t, True


def wijzig_medewerkers(t: Tabellen, nieuw: pd.DataFrame) -> tuple[Tabellen, list[str], list[str]]:
    oud = t["Employees"]
    nieuw = nieuw.copy()
    nieuw["medewerker"] = nieuw["medewerker"].map(_naam)

    fouten = []
    if dubbel := _dubbele(nieuw["medewerker"]):
        fouten.append(f"Deze namen komen dubbel voor: {', '.join(dubbel)}. Gebruik bijvoorbeeld ook een achternaam.")
    if nieuw["medewerker"].isna().any():
        fouten.append("Elke medewerker moet een naam hebben.")
    if fouten:
        return t, [], fouten

    hernoemd = _hernoemingen(oud, nieuw, "medewerker")
    over = set(nieuw["medewerker"])
    weg = {o for o in oud["medewerker"].map(_naam).dropna() if o not in hernoemd and o not in over}

    t = dict(t)
    t["Employees"] = nieuw
    meldingen = [f"'{o}' heet nu '{n}' (ook aangepast in de andere tabellen)." for o, n in hernoemd.items()]
    for tabel in ["CurrentSchedule", "MinimumUren", "Bijsturing"]:
        df = t[tabel].copy()
        df["medewerker"] = df["medewerker"].map(lambda e: hernoemd.get(e, e))
        voor = len(df)
        df = df[~df["medewerker"].isin(weg)]
        t[tabel] = df
        if voor - len(df):
            meldingen.append(f"{_meervoud(voor - len(df), 'regel', 'regels')} van verwijderde medewerkers weggehaald uit {tabel_label(tabel)}.")
    return t, meldingen, []


def wijzig_locaties(t: Tabellen, nieuw: pd.DataFrame) -> tuple[Tabellen, list[str], list[str]]:
    oud = t["Locaties"]
    nieuw = nieuw.copy()
    nieuw["locatie"] = nieuw["locatie"].map(_naam)
    nieuw = nieuw[nieuw["locatie"].notna()]

    if dubbel := _dubbele(nieuw["locatie"]):
        return t, [], [f"Deze locaties komen dubbel voor: {', '.join(dubbel)}."]

    hernoemd = _hernoemingen(oud, nieuw, "locatie")
    over = set(nieuw["locatie"])
    weg = {o for o in oud["locatie"].map(_naam).dropna() if o not in hernoemd and o not in over}

    in_gebruik = sorted(set(t["Requirements"]["locatie"].map(_naam)) & weg)
    if in_gebruik:
        return t, [], [
            f"Locatie {', '.join(in_gebruik)} heeft nog afdelingen. Verwijder of verplaats eerst die afdelingen."
        ]

    t = dict(t)
    t["Locaties"] = nieuw
    for tabel, kolom in [("Requirements", "locatie"), ("CurrentSchedule", "locatie"), ("MinimumUren", "locatie"),
                         ("Bijsturing", "locatie"), ("Config", "value")]:
        df = t[tabel].copy()
        df[kolom] = df[kolom].map(lambda l: hernoemd.get(l, l))
        t[tabel] = df
    meldingen = [f"Locatie '{o}' heet nu '{n}' (ook aangepast in de andere tabellen)." for o, n in hernoemd.items()]
    return t, meldingen, []


def wijzig_vereisten(t: Tabellen, nieuw: pd.DataFrame) -> tuple[Tabellen, list[str], list[str]]:
    oud = t["Requirements"]
    nieuw = nieuw.copy()
    for kol in ["afdeling", "locatie", "afdelingsgroep"]:
        nieuw[kol] = nieuw[kol].map(_naam)

    fouten = []
    if nieuw["afdeling"].isna().any() or nieuw["locatie"].isna().any():
        fouten.append("Elke regel moet een afdeling én een locatie hebben.")
    paren = nieuw["afdeling"] + " (" + nieuw["locatie"] + ")"
    if dubbel := _dubbele(paren):
        fouten.append(f"Deze afdelingen staan dubbel op dezelfde locatie: {', '.join(dubbel)}.")
    if pd.to_numeric(nieuw["vereiste_uren"], errors="coerce").isna().any():
        fouten.append("Vul bij elke afdeling de vereiste uren in.")
    if fouten:
        return t, [], fouten

    def paar(df, i):
        return (df.at[i, "afdeling"], df.at[i, "locatie"])

    paar_hernoemd = {}
    afd_hernoemd = {}
    for i in oud.index.intersection(nieuw.index):
        o, n = paar(oud, i), paar(nieuw, i)
        if o != n:
            paar_hernoemd[o] = n
            if o[0] != n[0]:
                afd_hernoemd[o[0]] = n[0]
    nieuwe_paren = set(zip(nieuw["afdeling"], nieuw["locatie"]))
    nieuwe_afdelingen = set(nieuw["afdeling"])
    # Een afdelingsnaam wordt alleen overal hernoemd als de oude naam nergens meer voorkomt.
    afd_hernoemd = {o: n for o, n in afd_hernoemd.items() if o not in nieuwe_afdelingen}

    t = dict(t)
    t["Requirements"] = nieuw
    meldingen = [f"Afdeling '{o}' heet nu '{n}' (ook aangepast in de andere tabellen)." for o, n in afd_hernoemd.items()]

    # Huidige planning: per (afdeling, locatie) meeverhuizen of weghalen.
    cs = t["CurrentSchedule"].copy()
    if len(cs):
        cs_paren = [paar_hernoemd.get((d, l), (d, l)) for d, l in zip(cs["afdeling"], cs["locatie"])]
        cs["afdeling"] = [d for d, _ in cs_paren]
        cs["locatie"] = [l for _, l in cs_paren]
        voor = len(cs)
        cs = cs[pd.Series([p in nieuwe_paren for p in cs_paren], index=cs.index, dtype=bool)]
        if voor - len(cs):
            meldingen.append(f"{_meervoud(voor - len(cs), 'regel', 'regels')} van verwijderde afdelingen weggehaald uit de huidige planning.")
    t["CurrentSchedule"] = cs

    # Minimumuren en bijsturing: afdelingsnaam meenemen; regels die niet meer kloppen weghalen.
    for tabel in ["MinimumUren", "Bijsturing"]:
        df = t[tabel].copy()
        df["afdeling"] = df["afdeling"].map(lambda d: afd_hernoemd.get(d, d))
        geldig = [
            _naam(d) is None or (d in nieuwe_afdelingen and (_naam(l) is None or (d, l) in nieuwe_paren))
            for d, l in zip(df["afdeling"], df["locatie"])
        ]
        voor = len(df)
        df = df[pd.Series(geldig, index=df.index, dtype=bool)]
        t[tabel] = df
        if voor - len(df):
            meldingen.append(f"{_meervoud(voor - len(df), 'regel', 'regels')} van verwijderde afdelingen weggehaald uit {tabel_label(tabel)}.")
    return t, meldingen, []


def tabel_label(tabel: str) -> str:
    return {
        "CurrentSchedule": "de huidige planning",
        "MinimumUren": "de extra eisen",
        "Bijsturing": "de bijsturing",
    }.get(tabel, tabel)
