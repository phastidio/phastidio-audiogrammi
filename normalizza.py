#!/usr/bin/env python3
"""
Script di normalizzazione glossario per sottotitoli SRT - progetto Phastidio.
Legge un file .srt grezzo (uscita di AssemblyAI) e i glossari YAML nella
cartella "glossari", e scrive un nuovo file .srt con i termini corretti.
NON tocca mai il file originale.

Prima di applicare qualsiasi sostituzione i glossari vengono validati: se una
voce e' scritta male lo script si ferma e spiega dove, invece di produrre una
trascrizione danneggiata.
"""

import sys
import re
import yaml
from pathlib import Path


# ---------------------------------------------------------------------------
# Caricamento e validazione dei glossari
# ---------------------------------------------------------------------------

def valida_voce(voce, nome_file, numero, errori):
    """Controlla una singola voce di glossario.
    Restituisce la voce se e' valida, oppure None aggiungendo il motivo a 'errori'."""
    etichetta = f"{nome_file}, voce n.{numero}"

    if not isinstance(voce, dict):
        errori.append(f"{etichetta}: voce malformata (deve iniziare con '- canonical:').")
        return None

    canonical = voce.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        etichetta = f"{nome_file}, voce '{canonical}'"
    else:
        errori.append(f"{etichetta}: manca la riga 'canonical', oppure non contiene un testo.")
        return None

    aliases = voce.get("aliases")

    if aliases is None:
        errori.append(f"{etichetta}: manca la riga 'aliases'.")
        return None

    # CONTROLLO 1 - alias scritto senza parentesi quadre.
    # Senza parentesi YAML lo legge come stringa e ogni LETTERA diventa un alias.
    if isinstance(aliases, str):
        errori.append(
            f"{etichetta}: 'aliases' e' scritto senza parentesi quadre.\n"
            f"        trovato:  aliases: {aliases}\n"
            f"        corretto: aliases: [{aliases}]\n"
            f"        Senza parentesi ogni singola lettera diventerebbe un alias."
        )
        return None

    if not isinstance(aliases, list) or len(aliases) == 0:
        errori.append(
            f"{etichetta}: 'aliases' deve essere un elenco non vuoto tra parentesi quadre, "
            f"es. aliases: [parolasbagliata]."
        )
        return None

    aliases_puliti = []
    for alias in aliases:
        if not isinstance(alias, str):
            errori.append(
                f"{etichetta}: l'alias {alias!r} non e' un testo. "
                f"Se e' un numero o un simbolo, racchiudilo tra virgolette."
            )
            return None
        alias = alias.strip()
        # CONTROLLO 2 - alias di un solo carattere.
        if len(alias) < 2:
            errori.append(
                f"{etichetta}: l'alias '{alias}' e' lungo un solo carattere.\n"
                f"        Sostituirebbe quella lettera in tutta la trascrizione."
            )
            return None
        aliases_puliti.append(alias)

    voce["aliases"] = aliases_puliti

    # CONTROLLO 3 - stesso problema su context_words, che ha la stessa struttura.
    context_words = voce.get("context_words")
    if context_words is not None:
        if isinstance(context_words, str):
            errori.append(
                f"{etichetta}: 'context_words' e' scritto senza parentesi quadre.\n"
                f"        corretto: context_words: [{context_words}]"
            )
            return None
        if not isinstance(context_words, list) or len(context_words) == 0:
            errori.append(
                f"{etichetta}: 'context_words' deve essere un elenco non vuoto tra parentesi quadre."
            )
            return None
        parole_pulite = []
        for parola in context_words:
            if not isinstance(parola, str) or not parola.strip():
                errori.append(f"{etichetta}: parola di contesto non valida ({parola!r}).")
                return None
            parole_pulite.append(parola.strip())
        voce["context_words"] = parole_pulite

    return voce


def carica_glossario(cartella_glossari):
    """Carica tutti i file .yml della cartella glossari, li unisce e li valida.
    Restituisce (elenco_voci_valide, elenco_errori)."""
    voci = []
    errori = []
    cartella = Path(cartella_glossari)

    for file_yml in sorted(cartella.glob("*.yml")):
        try:
            with open(file_yml, "r", encoding="utf-8") as f:
                contenuto = yaml.safe_load(f)
        except yaml.YAMLError as e:
            errori.append(f"{file_yml.name}: file YAML non leggibile ({e})")
            continue

        if contenuto is None:
            continue  # file vuoto o solo commenti: e' legittimo

        if not isinstance(contenuto, list):
            errori.append(
                f"{file_yml.name}: il file deve contenere un elenco di voci; "
                f"ogni voce inizia con '- canonical:'."
            )
            continue

        for numero, voce in enumerate(contenuto, start=1):
            voce_valida = valida_voce(voce, file_yml.name, numero, errori)
            if voce_valida is not None:
                voci.append(voce_valida)

    return voci, errori


# ---------------------------------------------------------------------------
# Sostituzioni
# ---------------------------------------------------------------------------

def compila_pattern(voce):
    """Costruisce un pattern regex per gli alias, con confini di parola, case-insensitive."""
    alias_pattern = "|".join(re.escape(a) for a in voce["aliases"])
    return re.compile(r"\b(" + alias_pattern + r")\b", re.IGNORECASE)


def contesto_presente(testo_esteso, context_words):
    """
    Verifica se una delle parole di contesto compare nel testo esteso
    (blocco corrente + un po' di testo prima e dopo, per gestire i casi
    in cui la trascrizione spezza una frase a meta' tra due blocchi SRT).
    Riconosce anche varianti con terminazione diversa (es. plurali):
    "model" trova sia "modello" che "modelli".
    """
    for cw in context_words:
        pattern = r"\b" + re.escape(cw) + r"\w*"
        if re.search(pattern, testo_esteso, re.IGNORECASE):
            return True
    return False


def applica_glossario(testo_blocco, testo_esteso, glossario, log_ambigue):
    """
    Applica tutte le voci del glossario al testo di un singolo blocco SRT.
    - testo_blocco: il testo del SOLO blocco corrente (qui avviene la sostituzione)
    - testo_esteso: blocco corrente + blocchi vicini (qui si cerca solo il contesto)
    """
    for voce in glossario:
        pattern = compila_pattern(voce)
        context_words = voce.get("context_words")

        if context_words:
            if not contesto_presente(testo_esteso, context_words):
                if pattern.search(testo_blocco):
                    log_ambigue.append((voce["canonical"], testo_blocco.strip()))
                continue

        testo_blocco = pattern.sub(voce["canonical"], testo_blocco)

    return testo_blocco


def normalizza_srt(percorso_input, percorso_output, glossario):
    with open(percorso_input, "r", encoding="utf-8") as f:
        contenuto = f.read()

    blocchi = contenuto.strip().split("\n\n")

    # Estraggo il solo testo (senza numero/timestamp) di ogni blocco,
    # per poterlo usare come "vicinato" quando controllo il contesto.
    testi_blocchi = []
    for blocco in blocchi:
        righe = blocco.split("\n")
        if len(righe) < 3:
            testi_blocchi.append("")
        else:
            testi_blocchi.append("\n".join(righe[2:]))

    blocchi_normalizzati = []
    log_ambigue = []

    for i, blocco in enumerate(blocchi):
        righe = blocco.split("\n")
        if len(righe) < 3:
            blocchi_normalizzati.append(blocco)
            continue

        numero = righe[0]
        timestamp = righe[1]
        testo_originale = "\n".join(righe[2:])

        # Testo esteso: blocco precedente + attuale + successivo,
        # solo per la ricerca delle parole di contesto.
        precedente = testi_blocchi[i - 1] if i > 0 else ""
        successivo = testi_blocchi[i + 1] if i < len(testi_blocchi) - 1 else ""
        testo_esteso = f"{precedente} {testo_originale} {successivo}"

        testo_corretto = applica_glossario(testo_originale, testo_esteso, glossario, log_ambigue)

        blocchi_normalizzati.append(f"{numero}\n{timestamp}\n{testo_corretto}")

    with open(percorso_output, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocchi_normalizzati) + "\n")

    return log_ambigue


# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 3:
        print("Uso: venv/bin/python3 normalizza.py episodio.srt episodio.normalized.srt")
        sys.exit(1)

    percorso_input = sys.argv[1]
    percorso_output = sys.argv[2]
    cartella_script = Path(__file__).parent
    cartella_glossari = cartella_script / "glossari"

    if not cartella_glossari.exists():
        print(f"Errore: cartella glossari non trovata in {cartella_glossari}")
        sys.exit(1)

    glossario, errori = carica_glossario(cartella_glossari)

    if errori:
        print("")
        print("=== GLOSSARIO NON VALIDO - nessun file e' stato modificato ===")
        print("")
        for errore in errori:
            print(f"  - {errore}")
        print("")
        print("Correggi le voci segnalate in glossari/ e rilancia lo script.")
        sys.exit(1)

    if not glossario:
        print(f"Errore: nessuna voce di glossario trovata in {cartella_glossari}")
        sys.exit(1)

    print(f"Caricate {len(glossario)} voci di glossario da {cartella_glossari}")

    log_ambigue = normalizza_srt(percorso_input, percorso_output, glossario)

    print(f"File normalizzato scritto in: {percorso_output}")

    if log_ambigue:
        print("\nATTENZIONE: occorrenze ambigue NON sostituite (nessuna parola di contesto nelle vicinanze):")
        for canonical, testo in log_ambigue:
            print(f"  - possibile '{canonical}' in: \"{testo}\"")
        print("Controlla questi punti a mano, se necessario.")


if __name__ == "__main__":
    main()
