"""
tools/check_normalize_regression.py — Rapport de non-régression de normalize_amount

Compare l'ancienne implémentation (v_before, copiée depuis la session courante avant
la réécriture) avec la nouvelle (validate.py actuel) sur toutes les valeurs de montants
présentes dans les fichiers raw/*.txt de toutes les exécutions existantes.

Usage :
    python tools\\check_normalize_regression.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from validate import normalize_amount as normalize_new


# ---------------------------------------------------------------------------
# Ancienne implémentation — copiée verbatim depuis la version précédente
# (lue dans le contexte de session ; aucun git disponible)
# ---------------------------------------------------------------------------

def normalize_amount_old(value):
    """Version originale avant la réécriture Phase A."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value)
    s = re.sub(r"[^\d,.\-]", "", s.replace("\u00a0", "").replace(" ", ""))
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif s.count(",") == 1:
        s = s.replace(",", ".")
    elif s.count(",") > 1:
        s = s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Extraction des valeurs de montants depuis les fichiers raw
# ---------------------------------------------------------------------------

AMOUNT_KEYS = {"net_amount", "vat_amount", "total_amount"}


def extract_amount_values(raw_text: str) -> dict[str, object]:
    """Parse le JSON dans un fichier raw et retourne les valeurs des 3 champs montant."""
    cleaned = re.sub(r"```(?:json)?", "", raw_text)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return {}
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return {k: obj[k] for k in AMOUNT_KEYS if k in obj}


def collect_all_raw_files() -> list[tuple[str, str, Path]]:
    """Retourne (run_name, invoice_id, path) pour chaque raw/*.txt existant."""
    entries = []
    runs_dirs = [ROOT / "runs"]
    # Inclure aussi les runs archivés
    archive = ROOT / "archive_v1_donnees_40" / "runs"
    if archive.exists():
        runs_dirs.append(archive)

    for runs_dir in runs_dirs:
        for run_dir in sorted(runs_dir.iterdir()):
            raw_dir = run_dir / "raw"
            if not raw_dir.exists():
                continue
            for f in sorted(raw_dir.glob("*.txt")):
                entries.append((run_dir.name, f.stem, f))
    return entries


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------

def main():
    entries = collect_all_raw_files()
    print(f"Corpus : {len(entries)} fichiers raw/*.txt dans {len({e[0] for e in entries})} runs\n")

    differences = []
    all_values = []  # pour statistiques

    for run_name, invoice_id, path in entries:
        raw = path.read_text(encoding="utf-8")
        amounts = extract_amount_values(raw)
        if not amounts:
            print(f"  AVERT  {run_name}/{invoice_id} : JSON non parsable — ignoré")
            continue

        for field, value in amounts.items():
            old_result = normalize_amount_old(value)
            new_result = normalize_new(value)
            all_values.append((run_name, invoice_id, field, value, old_result, new_result))

            if old_result != new_result:
                # Distinguer NaN / None
                def fmt(x):
                    return "None" if x is None else repr(x)
                differences.append({
                    "run": run_name,
                    "invoice_id": invoice_id,
                    "field": field,
                    "raw_value": repr(value),
                    "type": type(value).__name__,
                    "old": fmt(old_result),
                    "new": fmt(new_result),
                })

    print(f"Total valeurs comparées : {len(all_values)}")
    print(f"Différences trouvées    : {len(differences)}\n")

    if differences:
        print("DIFFÉRENCES :")
        for d in differences:
            print(f"  [{d['run']}] {d['invoice_id']}.{d['field']}")
            print(f"    valeur brute : {d['raw_value']} (type={d['type']})")
            print(f"    ancienne     : {d['old']}")
            print(f"    nouvelle     : {d['new']}")
    else:
        print("Aucune différence — non-régression confirmée.")

    # --- Focus dev_001 du run principal ---
    print("\n" + "="*60)
    print("FOCUS : dev_001 du run 20261104_154954_extraction_v0_dev")
    print("="*60)
    raw_001 = (ROOT / "runs" / "20261104_154954_extraction_v0_dev" / "raw" / "dev_001.txt"
               ).read_text(encoding="utf-8")
    amounts_001 = extract_amount_values(raw_001)
    print(f"Valeurs brutes dans le JSON du LLM :")
    for k, v in amounts_001.items():
        print(f"  {k} = {repr(v)}  (type={type(v).__name__})")
    print()

    # Vérité terrain dev_001
    gt_path = ROOT / "data" / "dev" / "ground_truth.csv"
    if gt_path.exists():
        import csv
        with open(gt_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["invoice_id"] == "dev_001":
                    print(f"Vérité terrain dev_001 :")
                    for k in ("net_amount", "vat_amount", "total_amount"):
                        print(f"  {k} = {row[k]}")
                    break

    print()
    print("Analyse des 2 erreurs amount_other signalées pour dev_001 :")
    for k, v in amounts_001.items():
        if k in ("vat_amount", "total_amount"):
            old_r = normalize_amount_old(v)
            new_r = normalize_new(v)
            print(f"  {k}: LLM a écrit {repr(v)} → ancienne={old_r} nouvelle={new_r}")
            print(f"    (identiques : {old_r == new_r})")


if __name__ == "__main__":
    main()
