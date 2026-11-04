"""
evaluate.py — Évaluation de l'étape 1 (extraction) par rapport à la vérité terrain

Compare le results.csv d'une exécution à data/<jeu>/ground_truth.csv et calcule :
  - taux JSON strict / tolérant, conformité au schéma
  - précision par champ, factures 100 % correctes
  - détection des incohérences : précision, rappel, détections justifiées
  - catégories d'erreurs d'extraction
  - précision par layout et par format de nombres
  - intervalle de confiance 95 % bootstrap sur « factures 100 % correctes »
  - test de McNemar exact (scipy) entre deux exécutions sur le même jeu

Utilisation (depuis invoice-pe/) :
    python src\\evaluate.py                        # dernière exécution
    python src\\evaluate.py runs\\<dossier>         # exécution précise
    python src\\evaluate.py runs\\A runs\\B          # plusieurs exécutions (+ McNemar si même jeu)
    python src\\evaluate.py --compare runs\\A runs\\B  # alias explicite pour la comparaison

Mode multi-répétitions :
    python src\\evaluate.py runs\\<run>_r1 runs\\<run>_r2 runs\\<run>_r3
    → affiche moyenne et écart-type des métriques

CHANGELOG evaluate.py
  2026-11-04  Ajout A4 :
              - catégories d'erreurs dans eval_details.csv
              - bootstrap CI 95 % (1000 tirages, graine 42)
              - McNemar exact (scipy) sur --compare
              - mode multi-répétitions (moyenne/écart-type)
"""

import csv
import hashlib
import json
import random
import sys
from pathlib import Path

import scipy.stats as stats

# Force UTF-8 sur stdout/stderr pour Windows (cp1252 ne supporte pas les accents)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["supplier_name", "invoice_date", "net_amount", "vat_amount", "total_amount"]
AMOUNTS = {"net_amount", "vat_amount", "total_amount"}
AMOUNT_TOL = 0.001          # deux montants sont égaux à 1 millime près
AMOUNT_X1000_RATIO_LO = 500   # ratio prédit/vrai entre 500 et 2000 → ×1000
AMOUNT_X1000_RATIO_HI = 2000
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def as_bool(x) -> bool:
    return str(x).strip().lower() == "true"


def norm_text(x) -> str:
    return " ".join(str(x or "").lower().split())


def field_correct(field, predicted, expected) -> bool:
    if predicted in ("", None):
        return False
    if field in AMOUNTS:
        try:
            return abs(float(predicted) - float(expected)) <= AMOUNT_TOL
        except ValueError:
            return False
    return norm_text(predicted) == norm_text(expected)


def rate(num, den):
    return round(num / den, 3) if den else None


# ---------------------------------------------------------------------------
# Catégorisation des erreurs d'extraction (A4)
# ---------------------------------------------------------------------------

def categorize_error(field: str, predicted, expected_str: str) -> str:
    """Retourne la catégorie d'erreur pour un champ incorrect.

    Catégories :
      supplier_extra_text  : prédit contient la vérité + texte supplémentaire
      amount_x1000         : rapport prédit/vrai ≈ 1000
      amount_other         : autre erreur de montant
      date_wrong           : date incorrecte ou manquante
      missing              : valeur vide ou None
      json_invalid         : utilisé au niveau de la facture quand json_lenient=False
    """
    pred_str = str(predicted).strip() if predicted not in ("", None) else ""

    if predicted in ("", None):
        return "missing"

    if field == "supplier_name":
        exp_norm = norm_text(expected_str)
        pred_norm = norm_text(pred_str)
        if exp_norm and exp_norm in pred_norm and pred_norm != exp_norm:
            return "supplier_extra_text"
        return "amount_other"   # erreur de nom autre

    if field == "invoice_date":
        return "date_wrong"

    if field in AMOUNTS:
        try:
            pred_val = float(predicted)
            exp_val = float(expected_str)
            if exp_val != 0:
                ratio = pred_val / exp_val
                if AMOUNT_X1000_RATIO_LO <= ratio <= AMOUNT_X1000_RATIO_HI:
                    return "amount_x1000"
                if 1 / AMOUNT_X1000_RATIO_HI <= ratio <= 1 / AMOUNT_X1000_RATIO_LO:
                    return "amount_x1000"
        except (ValueError, ZeroDivisionError):
            pass
        return "amount_other"

    return "amount_other"


# ---------------------------------------------------------------------------
# Bootstrap CI 95 % (A4)
# ---------------------------------------------------------------------------

def bootstrap_ci(values: list[bool], n: int = BOOTSTRAP_N,
                 seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """Intervalle de confiance 95 % par bootstrap sur la proportion de True."""
    rng = random.Random(seed)
    means = []
    k = len(values)
    for _ in range(n):
        sample = [rng.choice(values) for _ in range(k)]
        means.append(sum(sample) / k)
    means.sort()
    lo = means[int(0.025 * n)]
    hi = means[int(0.975 * n)]
    return round(lo, 4), round(hi, 4)


# ---------------------------------------------------------------------------
# McNemar exact (A4)
# ---------------------------------------------------------------------------

def mcnemar_exact(correct_a: list[bool], correct_b: list[bool]) -> dict:
    """Test de McNemar exact sur les paires (correct_A, correct_B).

    Table 2×2 :
              B=1   B=0
      A=1  [  n11   n10 ]
      A=0  [  n01   n00 ]

    McNemar s'intéresse aux cases discordantes (n10, n01).
    On utilise le test binomial exact (plus précis que chi² pour petits échantillons).
    """
    assert len(correct_a) == len(correct_b), "Les deux listes doivent avoir la même longueur"
    n11 = sum(a and b for a, b in zip(correct_a, correct_b))
    n10 = sum(a and not b for a, b in zip(correct_a, correct_b))
    n01 = sum(not a and b for a, b in zip(correct_a, correct_b))
    n00 = sum(not a and not b for a, b in zip(correct_a, correct_b))
    discordant = n10 + n01
    if discordant == 0:
        p_value = 1.0
    else:
        # Test binomial exact bilatéral : H0 → p(discordant = n10) = 0.5
        result = stats.binomtest(min(n10, n01), discordant, p=0.5, alternative="two-sided")
        p_value = result.pvalue
    return {
        "n11": n11, "n10": n10, "n01": n01, "n00": n00,
        "discordant": discordant,
        "p_value": round(p_value, 4),
        "significant_005": p_value < 0.05,
    }


# ---------------------------------------------------------------------------
# Évaluation d'un run
# ---------------------------------------------------------------------------

def evaluate_run(run_dir: Path) -> tuple[dict, list[dict]]:
    info = json.loads((run_dir / "run_info.json").read_text(encoding="utf-8"))
    results = list(csv.DictReader(open(run_dir / "results.csv", encoding="utf-8")))

    # --- Vérification d'empreinte de la vérité terrain ---
    gt_path = ROOT / "data" / info["dataset"] / "ground_truth.csv"
    current_sha = sha256_file(gt_path)
    stored_sha = info.get("ground_truth_sha256")

    if stored_sha is None:
        print(f"  AVERT [{run_dir.name}] : run_info.json ne contient pas d'empreinte "
              f"ground_truth_sha256. Ce run a ete cree avant la verification d'integrite.")
        print(f"  -> Empreinte actuelle : {current_sha[:16]}...")
        print(f"  -> Evaluation continuee avec avertissement. --compare refusera ce run.")
    elif stored_sha != current_sha:
        raise SystemExit(
            f"ERREUR : l'empreinte du ground_truth.csv a changé depuis l'exécution "
            f"'{run_dir.name}'.\n"
            f"  Empreinte stockée  : {stored_sha}\n"
            f"  Empreinte actuelle : {current_sha}\n"
            f"Évaluation refusée pour garantir la cohérence des résultats.\n"
            f"Si le jeu de données a été régénéré intentionnellement, "
            f"relancez l'extraction avec le nouveau jeu."
        )

    truth = {
        r["invoice_id"]: r
        for r in csv.DictReader(open(gt_path, encoding="utf-8"))
    }

    # Factures few-shot exclues des métriques si mentionnées dans run_info
    fewshot_ids = set(info.get("fewshot_ids_used") or [])

    details = []
    for r in results:
        gt = truth[r["invoice_id"]]
        ok = {f: field_correct(f, r.get(f), gt[f]) for f in FIELDS}
        rejected = not as_bool(r["is_valid"])
        is_fewshot = r["invoice_id"] in fewshot_ids

        # Catégorisation des erreurs par champ
        error_categories: dict[str, str] = {}
        if not as_bool(r.get("json_lenient", "False")):
            error_categories["_invoice"] = "json_invalid"
        else:
            for f in FIELDS:
                if not ok[f]:
                    error_categories[f] = categorize_error(f, r.get(f), gt[f])

        details.append({
            "invoice_id": r["invoice_id"],
            "layout": gt["layout"],
            "number_format": gt["number_format"],
            "json_strict": as_bool(r["json_strict"]),
            "json_lenient": as_bool(r["json_lenient"]),
            "schema_conform": as_bool(r["schema_ok"]) and as_bool(r["types_ok"]),
            **{f"{f}_ok": ok[f] for f in FIELDS},
            "all_fields_ok": all(ok.values()),
            "is_inconsistent": as_bool(gt["is_inconsistent"]),
            "rejected": rejected,
            "justified_detection": (
                rejected and as_bool(gt["is_inconsistent"]) and all(ok.values())),
            "latency_s": float(r["latency_s"]),
            "errors": r["errors"],
            "error_categories": ";".join(
                f"{k}:{v}" for k, v in error_categories.items()),
            "is_fewshot_example": is_fewshot,
            **{f"{f}_pred": r.get(f, "") for f in FIELDS},
            **{f"{f}_true": gt[f] for f in FIELDS},
        })

    # Métriques calculées sur les factures NON few-shot
    eval_details = [d for d in details if not d["is_fewshot_example"]]
    n = len(eval_details)
    if n == 0:
        raise SystemExit(f"Aucune facture à évaluer dans {run_dir} (toutes sont few-shot ?)")

    inc = [d for d in eval_details if d["is_inconsistent"]]
    rej = [d for d in eval_details if d["rejected"]]
    tp = sum(d["rejected"] and d["is_inconsistent"] for d in eval_details)

    # Comptage des catégories d'erreurs
    cat_counts: dict[str, int] = {}
    for d in eval_details:
        for cat_entry in d["error_categories"].split(";"):
            if ":" in cat_entry:
                _, cat = cat_entry.split(":", 1)
                cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # Bootstrap CI sur « factures 100 % correctes »
    exact_match_values = [d["all_fields_ok"] for d in eval_details]
    ci_lo, ci_hi = bootstrap_ci(exact_match_values)

    def group_accuracy(key):
        groups: dict[str, list[bool]] = {}
        for d in eval_details:
            groups.setdefault(d[key], []).append(d["all_fields_ok"])
        return {k: f"{sum(v)}/{len(v)}" for k, v in sorted(groups.items())}

    n_fewshot_excluded = len([d for d in details if d["is_fewshot_example"]])

    summary = {
        "run": info["run"],
        "prompt": info["prompt"],
        "model": info["model"],
        "dataset": info["dataset"],
        "ground_truth_sha256": stored_sha,   # None si run ancien sans empreinte
        "n_invoices": n,
        "n_fewshot_excluded": n_fewshot_excluded,
        "json_strict_rate": rate(sum(d["json_strict"] for d in eval_details), n),
        "json_lenient_rate": rate(sum(d["json_lenient"] for d in eval_details), n),
        "schema_conformance_rate": rate(sum(d["schema_conform"] for d in eval_details), n),
        **{f"acc_{f}": rate(sum(d[f"{f}_ok"] for d in eval_details), n) for f in FIELDS},
        "acc_all_fields_micro": rate(
            sum(d[f"{f}_ok"] for d in eval_details for f in FIELDS), n * len(FIELDS)),
        "invoice_exact_match_rate": rate(sum(d["all_fields_ok"] for d in eval_details), n),
        "exact_match_ci95_lo": ci_lo,
        "exact_match_ci95_hi": ci_hi,
        "inconsistency_precision": rate(tp, len(rej)),
        "inconsistency_recall": rate(tp, len(inc)),
        "justified_detections": f"{sum(d['justified_detection'] for d in eval_details)}/{len(inc)}",
        "false_rejections": sum(
            d["rejected"] and not d["is_inconsistent"] for d in eval_details),
        "error_categories": cat_counts,
        "exact_match_by_layout": group_accuracy("layout"),
        "exact_match_by_number_format": group_accuracy("number_format"),
        "mean_latency_s": round(sum(d["latency_s"] for d in eval_details) / n, 2) if n else None,
    }

    # Écriture des fichiers
    with open(run_dir / "eval_details.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(details[0].keys()))
        w.writeheader()
        w.writerows(details)
    (run_dir / "eval_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return summary, eval_details


# ---------------------------------------------------------------------------
# Comparaison de deux runs (McNemar + résumé côte à côte)
# ---------------------------------------------------------------------------

def compare_two_runs(run_a: Path, run_b: Path):
    """Charge les eval_details existants ou les calcule, puis compare."""
    print(f"\n{'='*60}")
    print(f"COMPARAISON")
    print(f"  A : {run_a.name}")
    print(f"  B : {run_b.name}")
    print(f"{'='*60}")

    sum_a, det_a = evaluate_run(run_a)
    sum_b, det_b = evaluate_run(run_b)

    if sum_a["dataset"] != sum_b["dataset"]:
        print(f"AVERTISSEMENT : jeux différents ({sum_a['dataset']} vs {sum_b['dataset']}). "
              f"McNemar ignoré.")
        return sum_a, sum_b, None

    # --- Vérification de cohérence des empreintes ---
    sha_a = sum_a.get("ground_truth_sha256")
    sha_b = sum_b.get("ground_truth_sha256")
    if sha_a is None or sha_b is None:
        missing = []
        if sha_a is None:
            missing.append(f"A ({run_a.name})")
        if sha_b is None:
            missing.append(f"B ({run_b.name})")
        raise SystemExit(
            f"ERREUR --compare refusé : empreinte ground_truth_sha256 absente pour "
            f"{', '.join(missing)}.\n"
            f"Ces runs ont été créés avant l'ajout de la vérification d'intégrité.\n"
            f"Relancez l'extraction pour obtenir des runs comparables."
        )
    if sha_a != sha_b:
        raise SystemExit(
            f"ERREUR --compare refusé : les deux runs ont des empreintes différentes.\n"
            f"  A ({run_a.name}) : {sha_a}\n"
            f"  B ({run_b.name}) : {sha_b}\n"
            f"Ils ont été produits sur des versions différentes du ground_truth.csv.\n"
            f"Comparer leurs métriques serait trompeur."
        )

    # Aligner sur les invoice_ids communs
    ids_a = {d["invoice_id"]: d for d in det_a}
    ids_b = {d["invoice_id"]: d for d in det_b}
    common_ids = sorted(set(ids_a) & set(ids_b))
    if not common_ids:
        print("Aucune facture commune — McNemar impossible.")
        return sum_a, sum_b, None

    # McNemar global (facture 100 % correcte)
    correct_a = [ids_a[i]["all_fields_ok"] for i in common_ids]
    correct_b = [ids_b[i]["all_fields_ok"] for i in common_ids]
    mc_global = mcnemar_exact(correct_a, correct_b)

    # McNemar par champ
    mc_by_field = {}
    for f in FIELDS:
        ca = [ids_a[i][f"{f}_ok"] for i in common_ids]
        cb = [ids_b[i][f"{f}_ok"] for i in common_ids]
        mc_by_field[f] = mcnemar_exact(ca, cb)

    comparison = {
        "run_a": run_a.name,
        "run_b": run_b.name,
        "dataset": sum_a["dataset"],
        "n_common": len(common_ids),
        "mcnemar_exact_match": mc_global,
        "mcnemar_by_field": mc_by_field,
        "summary_a": sum_a,
        "summary_b": sum_b,
    }

    # Affichage table 2×2 et p-values
    pct = lambda x: "—" if x is None else f"{x*100:5.1f}%"
    print(f"\n  Factures en commun : {len(common_ids)}")
    print(f"\n  Factures 100% correctes")
    print(f"    A : {pct(sum_a['invoice_exact_match_rate'])}  "
          f"IC95% [{sum_a['exact_match_ci95_lo']*100:.1f}% – {sum_a['exact_match_ci95_hi']*100:.1f}%]")
    print(f"    B : {pct(sum_b['invoice_exact_match_rate'])}  "
          f"IC95% [{sum_b['exact_match_ci95_lo']*100:.1f}% – {sum_b['exact_match_ci95_hi']*100:.1f}%]")
    mc = mc_global
    print(f"\n  McNemar exact (facture 100% correcte) — table 2×2 :")
    print(f"              B=correct  B=faux")
    print(f"    A=correct    {mc['n11']:>4}       {mc['n10']:>4}")
    print(f"    A=faux       {mc['n01']:>4}       {mc['n00']:>4}")
    print(f"    Discordants : {mc['discordant']}  p-value : {mc['p_value']}"
          + ("  *** p<0.05" if mc["significant_005"] else "  (non significatif)"))

    print(f"\n  McNemar par champ :")
    for f in FIELDS:
        mc_f = mc_by_field[f]
        acc_a = sum_a.get(f"acc_{f}")
        acc_b = sum_b.get(f"acc_{f}")
        sig = "  ***" if mc_f["significant_005"] else ""
        print(f"    {f:<20} A={pct(acc_a)} B={pct(acc_b)}  "
              f"discordants={mc_f['discordant']}  p={mc_f['p_value']}{sig}")

    return sum_a, sum_b, comparison


# ---------------------------------------------------------------------------
# Mode multi-répétitions
# ---------------------------------------------------------------------------

def summarize_repeats(summaries: list[dict]):
    """Affiche moyenne et écart-type des métriques numériques sur N répétitions."""
    import math
    rate_keys = [
        "json_strict_rate", "json_lenient_rate", "schema_conformance_rate",
        "invoice_exact_match_rate",
    ] + [f"acc_{f}" for f in FIELDS]
    raw_keys = ["mean_latency_s"]

    print(f"\n{'='*60}")
    print(f"RÉSUMÉ MULTI-RÉPÉTITIONS ({len(summaries)} runs)")
    print(f"  Prompt : {summaries[0]['prompt']}  Modèle : {summaries[0]['model']}")
    print(f"{'='*60}")
    for k in rate_keys + raw_keys:
        vals = [s[k] for s in summaries if s.get(k) is not None]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        std = math.sqrt(var)
        if k in rate_keys:
            print(f"  {k:<30} moy={mean*100:5.1f}%  std={std*100:4.1f}%")
        else:
            print(f"  {k:<30} moy={mean:6.2f}s  std={std:5.2f}s")


# ---------------------------------------------------------------------------
# Mise à jour comparison.csv
# ---------------------------------------------------------------------------

def append_comparison(summary: dict):
    path = ROOT / "runs" / "comparison.csv"
    flat = {
        k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v)
        for k, v in summary.items()
    }
    existing = (
        list(csv.DictReader(open(path, encoding="utf-8"))) if path.exists() else []
    )
    existing = [r for r in existing if r.get("run") != summary["run"]]
    existing.append(flat)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(flat.keys()))
        w.writeheader()
        w.writerows(existing)


# ---------------------------------------------------------------------------
# Affichage d'un résumé
# ---------------------------------------------------------------------------

def print_summary(s: dict, details: list[dict]):
    pct = lambda x: "—" if x is None else f"{x*100:5.1f}%"
    print(f"\n=== {s['run']}  ({s['prompt']}, {s['model']}, {s['dataset']}, "
          f"{s['n_invoices']} factures évaluées"
          + (f", {s['n_fewshot_excluded']} few-shot exclus" if s["n_fewshot_excluded"] else "")
          + ")")
    print(f"  JSON strict / tolérant   : {pct(s['json_strict_rate'])} / {pct(s['json_lenient_rate'])}")
    print(f"  Conformité au schéma     : {pct(s['schema_conformance_rate'])}")
    for f in FIELDS:
        print(f"  Précision {f:<18}: {pct(s['acc_' + f])}")
    ci = f"IC95% [{s['exact_match_ci95_lo']*100:.1f}% – {s['exact_match_ci95_hi']*100:.1f}%]"
    print(f"  Factures 100% correctes  : {pct(s['invoice_exact_match_rate'])}  {ci}")
    print(f"  Incohérences — précision {pct(s['inconsistency_precision'])} · "
          f"rappel {pct(s['inconsistency_recall'])} · "
          f"détections justifiées {s['justified_detections']} · "
          f"faux rejets {s['false_rejections']}")
    if s.get("error_categories"):
        print(f"  Catégories d'erreurs     : "
              + "  ".join(f"{k}={v}" for k, v in sorted(s["error_categories"].items())))
    print(f"  Par layout               : {s['exact_match_by_layout']}")
    print(f"  Par format de nombres    : {s['exact_match_by_number_format']}")
    print(f"  Latence moyenne          : {s['mean_latency_s']} s")

    wrong = [d for d in details if not d["all_fields_ok"]]
    if wrong:
        print("  Erreurs d'extraction :")
        for d in wrong:
            bad = []
            for f in FIELDS:
                if not d[f"{f}_ok"]:
                    cat = ""
                    for entry in d["error_categories"].split(";"):
                        if entry.startswith(f + ":"):
                            cat = f" [{entry.split(':',1)[1]}]"
                    bad.append(
                        f"{f}: {d[f+'_pred'] or 'vide'!r} → {d[f+'_true']!r}{cat}")
            print(f"    - {d['invoice_id']} ({d['layout']}, nombres {d['number_format']}) : "
                  + " | ".join(bad))


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def main():
    # Détection du mode --compare (peut être positionnel ou flag)
    raw_args = sys.argv[1:]
    if "--compare" in raw_args:
        raw_args.remove("--compare")

    runs_input = [Path(a) for a in raw_args if not a.startswith("--")]

    if not runs_input:
        candidates = sorted(p for p in (ROOT / "runs").iterdir()
                            if p.is_dir() and (p / "results.csv").exists())
        if not candidates:
            raise SystemExit("Aucune exécution trouvée dans runs/ — lancez d'abord extract.py")
        runs_input = [candidates[-1]]

    # Résoudre les chemins (relatifs → absolus depuis ROOT)
    runs: list[Path] = []
    for r in runs_input:
        p = r if r.is_absolute() else ROOT / r
        if not p.exists():
            raise SystemExit(f"Dossier introuvable : {p}")
        runs.append(p)

    summaries = []
    all_details = []
    for run in runs:
        s, d = evaluate_run(run)
        append_comparison(s)
        print_summary(s, d)
        summaries.append(s)
        all_details.append(d)

    # Comparaison McNemar si exactement 2 runs
    if len(runs) == 2:
        compare_two_runs(runs[0], runs[1])

    # Mode multi-répétitions : même prompt, même modèle, même jeu, N >= 2
    if len(summaries) >= 2:
        same_prompt = len({s["prompt"] for s in summaries}) == 1
        same_model = len({s["model"] for s in summaries}) == 1
        same_dataset = len({s["dataset"] for s in summaries}) == 1
        if same_prompt and same_model and same_dataset:
            summarize_repeats(summaries)

    print(f"\nTableau comparatif mis à jour : {ROOT / 'runs' / 'comparison.csv'}")


if __name__ == "__main__":
    main()
