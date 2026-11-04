"""
extract.py — Étape 1 : extraction des factures par le LLM + validation Python

Pour chaque facture d'un jeu (dev ou test) :
  1. on insère le texte de la facture dans le prompt choisi (prompts/<nom>.txt)
  2. optionnellement, on injecte des exemples few-shot via {fewshot}
  3. on appelle le LLM local via Ollama (température 0, graine fixe)
  4. on valide la réponse avec validate.py
  5. on enregistre tout : réponse brute, valeurs normalisées, contrôles, latence

Chaque exécution (ou répétition) crée un dossier runs/<date>_<prompt>_<jeu>[_rN]/ :
  - run_info.json   : modèle, prompt, hash, paramètres, jeu, date, répétition
  - results.csv     : une ligne par facture
  - raw/            : réponse brute du LLM pour chaque facture

Utilisation (depuis invoice-pe/) :
    python src\\extract.py --set dev --prompt extraction_v0 --model qwen2.5:7b
    python src\\extract.py --set dev --prompt extraction_v0 --model qwen2.5:7b --limit 3
    python src\\extract.py --set dev --prompt extraction_v1 --model qwen2.5:7b --fewshot fewshot_v1
    python src\\extract.py --set dev --prompt extraction_v0 --model qwen2.5:7b --repeat 3
    python src\\extract.py --set test --prompt extraction_v1 --model qwen2.5:7b --confirm-test

Règle jeu de test :
    --set test exige --confirm-test ET la saisie manuelle de "OUI" au démarrage.
    Sans --confirm-test, le script s'arrête immédiatement sans rien créer.
"""

import argparse
import csv
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import ollama

from validate import parse_json, validate

ROOT = Path(__file__).resolve().parent.parent      # dossier invoice-pe


def sha256_file(path: Path) -> str:
    """SHA-256 hex d'un fichier, pour traçabilité de la vérité terrain."""
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Paramètres du modèle : IDENTIQUES pour toutes les versions de prompt — NE PAS MODIFIER
MODEL_OPTIONS = {
    "temperature": 0,
    "seed": 42,
    "num_ctx": 4096,
    "num_predict": 512,
}

RESULT_FIELDS = ["invoice_id", "latency_s", "json_strict", "json_lenient",
                 "schema_ok", "required_ok", "types_ok", "arithmetic_ok", "vat_rate_ok", "is_valid",
                 "supplier_name", "invoice_date", "net_amount", "vat_amount", "total_amount", "errors"]


# ---------------------------------------------------------------------------
# Chargement du prompt
# ---------------------------------------------------------------------------

def load_prompt(name: str) -> tuple[str, str]:
    """Charge prompts/<name>.txt et retourne (texte, sha256_12)."""
    path = ROOT / "prompts" / f"{name}.txt"
    if not path.exists():
        raise SystemExit(f"Prompt introuvable : {path}\nVérifiez le nom (sans .txt).")
    text = path.read_text(encoding="utf-8")
    if "{invoice}" not in text:
        raise SystemExit(f"Le prompt {path} doit contenir le marqueur {{invoice}}")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return text, sha


# ---------------------------------------------------------------------------
# Chargement des exemples few-shot (A2)
# ---------------------------------------------------------------------------

def load_fewshot(name: str, exclude_ids: set[str] | None = None) -> tuple[str, list[str]]:
    """Charge prompts/fewshot/<name>.json.

    Format attendu du fichier JSON :
    {
      "description": "...",          // optionnel
      "examples": [
        {
          "invoice_id": "dev_001",   // id dans data/dev/
          "expected": { ... }        // JSON attendu tel qu'il apparaîtra dans le prompt
        },
        ...
      ]
    }

    Retourne (bloc_texte_à_injecter, liste_des_invoice_ids_utilisés).
    Les ids retournés doivent être exclus du calcul des métriques dev.
    """
    path = ROOT / "prompts" / "fewshot" / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"Fichier few-shot introuvable : {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    examples = data.get("examples", [])
    if not examples:
        raise SystemExit(f"Le fichier few-shot {path} ne contient aucun exemple.")

    used_ids = []
    blocks = []
    for ex in examples:
        inv_id = ex["invoice_id"]
        if exclude_ids and inv_id in exclude_ids:
            continue
        inv_path = ROOT / "data" / "dev" / "invoices" / f"{inv_id}.txt"
        if not inv_path.exists():
            raise SystemExit(f"Facture few-shot introuvable : {inv_path}")
        invoice_text = inv_path.read_text(encoding="utf-8").strip()
        expected_json = json.dumps(ex["expected"], ensure_ascii=False)
        blocks.append(f"Facture :\n{invoice_text}\n\nRéponse attendue :\n{expected_json}")
        used_ids.append(inv_id)

    if not blocks:
        raise SystemExit("Aucun exemple few-shot utilisable après filtrage.")

    fewshot_text = "\n\n---\n\n".join(blocks)
    return fewshot_text, used_ids


# ---------------------------------------------------------------------------
# Appel LLM
# ---------------------------------------------------------------------------

def call_llm(model: str, prompt: str) -> tuple[str, float]:
    start = time.perf_counter()
    response = ollama.generate(model=model, prompt=prompt, options=MODEL_OPTIONS)
    return response["response"], round(time.perf_counter() - start, 2)


# ---------------------------------------------------------------------------
# Confirmation jeu de test (A1 — règle méthodologique)
# ---------------------------------------------------------------------------

def confirm_test_set() -> datetime:
    """Demande une confirmation explicite avant tout traitement du jeu de test.
    Lève SystemExit si refusée ou interrompue. Retourne l'heure de confirmation.
    """
    print("=" * 60)
    print("ATTENTION : vous demandez l'exécution sur le JEU DE TEST.")
    print("Le jeu de test ne doit jamais servir à concevoir les prompts.")
    print("Tapez  OUI  (en majuscules) pour confirmer, autre chose pour annuler.")
    print("=" * 60)
    try:
        answer = input("Confirmation : ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAnnulé.")
        raise SystemExit(0)
    if answer != "OUI":
        print("Annulé — aucun dossier créé dans runs/.")
        raise SystemExit(0)
    return datetime.now()


# ---------------------------------------------------------------------------
# Exécution d'un seul run
# ---------------------------------------------------------------------------

def run_once(args, template: str, prompt_hash: str, files: list[Path],
             fewshot_text: str | None, fewshot_ids: list[str],
             suffix: str = "", test_confirmed_at: str | None = None) -> Path:
    """Lance une exécution complète et retourne le chemin du dossier créé."""

    run_name = (f"{datetime.now():%Y%m%d_%H%M%S}_{args.prompt}_{args.set}"
                + (f"_{args.tag}" if args.tag else "")
                + suffix)
    run_dir = ROOT / "runs" / run_name
    (run_dir / "raw").mkdir(parents=True)

    # run_info.json
    gt_path = ROOT / "data" / args.set / "ground_truth.csv"
    info = {
        "run": run_name,
        "stage": "extraction",
        "dataset": args.set,
        "n_invoices": len(files),
        "prompt": args.prompt,
        "prompt_sha256_12": prompt_hash,
        "model": args.model,
        "model_options": MODEL_OPTIONS,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "ground_truth_sha256": sha256_file(gt_path),
        "fewshot_file": args.fewshot if args.fewshot else None,
        "fewshot_ids_used": fewshot_ids if fewshot_ids else None,
        "repeat_suffix": suffix if suffix else None,
    }
    if test_confirmed_at:
        info["test_confirmed"] = True
        info["test_confirmed_at"] = test_confirmed_at
    (run_dir / "run_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nExécution {run_name}")
    print(f"  {len(files)} factures · modèle {args.model} · prompt {args.prompt}"
          + (f" · few-shot {args.fewshot}" if args.fewshot else ""))
    if fewshot_ids:
        print(f"  Exemples few-shot (exclus des métriques dev) : {', '.join(fewshot_ids)}")

    rows = []
    for i, path in enumerate(files, 1):
        invoice_id = path.stem

        # Construction du prompt final
        prompt_text = template.replace("{invoice}", path.read_text(encoding="utf-8"))
        if fewshot_text is not None:
            if "{fewshot}" not in prompt_text:
                raise SystemExit(
                    f"Le prompt {args.prompt} ne contient pas le marqueur {{fewshot}} "
                    f"mais --fewshot a été fourni.")
            prompt_text = prompt_text.replace("{fewshot}", fewshot_text)

        try:
            raw, latency = call_llm(args.model, prompt_text)
        except Exception as e:
            raise SystemExit(
                f"Erreur LLM : {e}\n"
                f"Vérifiez qu'Ollama tourne et que le modèle est installé "
                f"(ollama pull {args.model}).")

        (run_dir / "raw" / f"{invoice_id}.txt").write_text(raw, encoding="utf-8")

        obj, strict, lenient = parse_json(raw)
        v = validate(obj)

        row = {
            "invoice_id": invoice_id,
            "latency_s": latency,
            "json_strict": strict,
            "json_lenient": lenient,
            **{k: v[k] for k in RESULT_FIELDS if k in v},
            "errors": ";".join(v["errors"]),
        }
        rows.append(row)
        status = "OK " if v["is_valid"] else "REJ"
        print(f"  [{i:>2}/{len(files)}] {invoice_id}  {status}  {latency:>5.1f}s"
              + (f"  {';'.join(v['errors'])}" if v["errors"] else ""))

    with open(run_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        w.writeheader()
        w.writerows(rows)

    n = len(rows)
    print(f"  → JSON strict {sum(r['json_strict'] for r in rows)}/{n} · "
          f"JSON tolérant {sum(r['json_lenient'] for r in rows)}/{n} · "
          f"valides {sum(r['is_valid'] for r in rows)}/{n} · "
          f"latence moy. {sum(r['latency_s'] for r in rows)/n:.1f}s")
    print(f"  → Dossier : {run_dir}")
    return run_dir


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Extraction des factures par LLM")
    parser.add_argument("--set", choices=["dev", "test"], default="dev",
                        help="jeu de factures (test exige --confirm-test)")
    parser.add_argument("--prompt", default="extraction_v0",
                        help="nom du fichier dans prompts/ (sans .txt)")
    parser.add_argument("--model", required=True,
                        help="modèle Ollama, ex. qwen2.5:7b")
    parser.add_argument("--limit", type=int, default=None,
                        help="ne traiter que les N premières factures (test rapide)")
    parser.add_argument("--tag", default="",
                        help="étiquette libre ajoutée au nom du dossier")
    # A1 — répétitions
    parser.add_argument("--repeat", type=int, default=1,
                        help="nombre de répétitions identiques (dossiers suffixés _r1, _r2…)")
    # A2 — few-shot
    parser.add_argument("--fewshot", default=None,
                        help="nom du fichier dans prompts/fewshot/ (sans .json)")
    # Règle jeu de test
    parser.add_argument("--confirm-test", action="store_true",
                        help="requis pour --set test ; déclenche la confirmation manuelle")
    args = parser.parse_args()

    # --- Garde jeu de test ---
    test_confirmed_at = None
    if args.set == "test":
        if not args.confirm_test:
            print("ERREUR : --set test exige --confirm-test.")
            print("Relancez avec --confirm-test et confirmez manuellement.")
            raise SystemExit(1)
        confirmed_dt = confirm_test_set()   # lève SystemExit si refus
        test_confirmed_at = confirmed_dt.isoformat(timespec="seconds")

    # --- Chargement du prompt ---
    template, prompt_hash = load_prompt(args.prompt)

    # --- Chargement des exemples few-shot ---
    fewshot_text = None
    fewshot_ids: list[str] = []
    if args.fewshot:
        fewshot_text, fewshot_ids = load_fewshot(args.fewshot)

    # --- Liste des factures ---
    data_dir = ROOT / "data" / args.set
    files = sorted((data_dir / "invoices").glob("*.txt"))[: args.limit]
    if not files:
        raise SystemExit(
            f"Aucune facture trouvée dans {data_dir / 'invoices'}\n"
            f"Lancez d'abord generator.py.")

    # Exclure les factures few-shot de la liste si on est sur dev
    if fewshot_ids and args.set == "dev":
        excluded = set(fewshot_ids)
        files_run = [f for f in files if f.stem not in excluded]
        if len(files_run) < len(files):
            print(f"  Note : {len(files) - len(files_run)} facture(s) exclue(s) du run "
                  f"(utilisée(s) comme exemples few-shot) : {', '.join(fewshot_ids)}")
    else:
        files_run = files

    # --- Boucle de répétitions ---
    if args.repeat < 1:
        raise SystemExit("--repeat doit être >= 1")

    suffixes = [""] if args.repeat == 1 else [f"_r{i}" for i in range(1, args.repeat + 1)]

    for suffix in suffixes:
        run_once(args, template, prompt_hash, files_run,
                 fewshot_text, fewshot_ids, suffix, test_confirmed_at)

    if args.repeat > 1:
        print(f"\n{args.repeat} répétitions terminées.")
        print("Utilisez evaluate.py avec les dossiers _r1…_r{args.repeat} pour mesurer la variabilité.")


if __name__ == "__main__":
    main()
