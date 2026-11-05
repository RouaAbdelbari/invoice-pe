"""
evaluate_sql.py — Évaluation du Text-to-SQL (Phase C4)

Évalue un run text2sql sur trois niveaux (distingués clairement) :
  N1 — Syntaxe     : la requête est syntaxiquement valide (sqlglot)
  N2 — Garde       : la requête est acceptée par sql_guard
  N3 — Sémantique  : le résultat correspond à celui de reference_sql sur le même schéma
                     (comparaison d'ensembles de lignes, ordre ignoré sauf si ORDER BY
                     explicite dans reference_sql, arrondi à 3 décimales)

Métriques additionnelles :
  - Exactitude des nombres : chaque nombre cité dans la réponse figure dans les lignes
  - Taux de non-réponse (garde refusée + erreur d'exécution)

Utilisation :
    python src\\evaluate_sql.py runs\\<run_text2sql>
    python src\\evaluate_sql.py --propagation runs\\<run_gold> runs\\<run_pipeline>

Fichiers écrits dans le dossier du run :
    eval_sql.csv        une ligne par question
    eval_sql_summary.json  métriques globales
"""

import argparse
import csv
import json
import math
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import sqlglot

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import connect_readonly
from sql_guard import check as guard_check

# Importer les fonctions de vérification des nombres depuis text2sql
# (partagé entre text2sql.py et evaluate_sql.py)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("_t2sql_nums", ROOT / "src" / "text2sql.py")
# Ne pas exécuter text2sql.py en entier — on importe juste les fonctions
# On les redéfinit ici pour éviter les dépendances circulaires
import re as _re

def _extract_numbers_french_aware(text: str, question: str) -> list[str]:
    """Vérificateur de nombres corrigé (formats français, ignore bruits)."""
    q_nums = set(_re.findall(r"\b\d{4}\b", question))
    q_all_nums = set(_re.findall(r"\b\d+(?:[.,]\d+)?\b", question))
    cleaned = _re.sub(r"^\s*\d+[.)]\s", "", text, flags=_re.MULTILINE)
    patterns = [
        r"\b\d{1,3}(?:\s\d{3})+(?:,\d+)?\b",
        r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b",
        r"\b\d+(?:[.,]\d+)?\b",
    ]
    found = []
    for p in patterns:
        for m in _re.finditer(p, cleaned):
            val = m.group()
            if val in q_nums:
                continue
            val_clean = val.replace(" ", "").replace(",", ".")
            q_clean = {n.replace(" ", "").replace(",", ".") for n in q_all_nums}
            if val_clean in q_clean:
                continue
            if val not in found:
                found.append(val)
    return found


def _normalize_num(s: str) -> float | None:
    s = s.strip()
    if " " in s and "," in s:
        s = s.replace(" ", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(" ", "").replace(",", ".")
    elif "," in s and "." in s:
        if s.rfind(".") > s.rfind(","):
            s = s.replace(",", "").replace(" ", "")
        else:
            s = s.replace(".", "").replace(",", ".").replace(" ", "")
    else:
        s = s.replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


def _verify_numbers(answer: str, rows: list[dict], question: str) -> tuple[list[str], list[str]]:
    cited = _extract_numbers_french_aware(answer, question)
    if not cited:
        return [], []
    row_values: set[float] = set()
    for row in rows:
        for v in row.values():
            if v is None:
                continue
            n = _normalize_num(str(v))
            if n is not None:
                row_values.add(round(n, 3))
    unverified = [c for c in cited
                  if (n := _normalize_num(c)) is not None
                  and not any(abs(n - rv) <= 0.001 for rv in row_values)]
    return cited, unverified

DECIMAL_PLACES = 3
TOLERANCE = Decimal("0.001")


# ---------------------------------------------------------------------------
# Utilitaires de comparaison
# ---------------------------------------------------------------------------

def _to_decimal(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.001"))
    except InvalidOperation:
        return None


def _normalize_value(v):
    """Normalise une valeur pour la comparaison : Decimal pour les numériques, str sinon."""
    d = _to_decimal(v)
    if d is not None:
        return d
    return str(v).strip().lower() if v is not None else None


def _normalize_row(row: dict) -> frozenset:
    """Convertit une ligne en frozenset de paires (clé, valeur_normalisée)."""
    return frozenset((k.lower(), _normalize_value(v)) for k, v in row.items())


def _rows_equal(rows_a: list[dict], rows_b: list[dict],
                ordered: bool = False) -> bool:
    """Compare deux ensembles de lignes.

    Si ordered=False : comparaison multi-ensemble (Counter).
    Si ordered=True  : comparaison séquentielle.
    Tolérance numérique : 0.001 (3 décimales).
    """
    if len(rows_a) != len(rows_b):
        return False
    if ordered:
        return all(_row_approx_equal(a, b) for a, b in zip(rows_a, rows_b))
    # Non ordonné : tri par représentation normalisée
    norm_a = sorted(_normalize_row(r) for r in rows_a)
    norm_b = sorted(_normalize_row(r) for r in rows_b)
    return norm_a == norm_b


def _row_approx_equal(a: dict, b: dict) -> bool:
    if set(a.keys()) != set(b.keys()):
        return False
    for k in a:
        va, vb = _normalize_value(a[k]), _normalize_value(b[k])
        if isinstance(va, Decimal) and isinstance(vb, Decimal):
            if abs(va - vb) > TOLERANCE:
                return False
        elif va != vb:
            return False
    return True


def _reference_has_order_by(sql: str) -> bool:
    """Détecte si la reference_sql contient un ORDER BY explicite."""
    try:
        trees = sqlglot.parse(sql, dialect="postgres")
        if not trees:
            return False
        for node in trees[0].walk():
            if isinstance(node, sqlglot.expressions.Order):
                return True
    except Exception:
        pass
    return False


def _reference_has_limit(sql: str) -> bool:
    """Détecte si la reference_sql contient un LIMIT."""
    try:
        trees = sqlglot.parse(sql, dialect="postgres")
        if not trees:
            return False
        for node in trees[0].walk():
            if isinstance(node, sqlglot.expressions.Limit):
                return True
    except Exception:
        pass
    return False


def _reference_has_rank_or_topn(sql: str) -> bool:
    """Détecte si la reference_sql contient RANK(), DENSE_RANK(), ROW_NUMBER()
    ou un LIMIT qui indique un classement (Top-N)."""
    sql_upper = sql.upper()
    if any(fn in sql_upper for fn in ("RANK(", "DENSE_RANK(", "ROW_NUMBER(")):
        return True
    if _reference_has_limit(sql):
        return True
    return False


def _rows_equal_loose(ref_rows, cand_rows, ref_sql) -> bool:
    """Comparaison N3 souple :
    - Ignore les noms de colonnes
    - Ignore l'ordre des lignes, sauf si ORDER BY + (LIMIT ou classement)
    - Colonnes en plus acceptées si toutes les colonnes de la référence s'y retrouvent
    - Une colonne manquante est un échec
    - Arrondi à 3 décimales
    """
    if len(ref_rows) != len(cand_rows):
        return False

    # Déterminer si l'ordre compte
    order_matters = _reference_has_order_by(ref_sql) and _reference_has_rank_or_topn(ref_sql)

    # Extraire les valeurs sans les noms de colonnes
    ref_vals = [tuple(_normalize_value(v) for v in row.values()) for row in ref_rows]
    cand_vals = [tuple(_normalize_value(v) for v in row.values()) for row in cand_rows]

    # Vérifier que le candidat a au moins autant de colonnes que la référence
    if len(ref_vals) > 0 and len(cand_vals) > 0:
        if len(cand_vals[0]) < len(ref_vals[0]):
            return False  # colonne manquante

    # Si colonnes en plus dans le candidat, tronquer aux colonnes de la référence
    n_ref_cols = len(ref_vals[0]) if ref_vals else 0
    cand_trimmed = [tuple(v[:n_ref_cols]) for v in cand_vals] if n_ref_cols else cand_vals

    if order_matters:
        # Comparaison séquentielle (ordonnée)
        return _tuples_approx_equal_seq(ref_vals, cand_trimmed)
    else:
        # Comparaison non ordonnée (multi-ensemble)
        return sorted(ref_vals) == sorted(cand_trimmed)


def _tuples_approx_equal_seq(a_list, b_list) -> bool:
    """Comparaison séquentielle de tuples avec tolérance numérique."""
    for a, b in zip(a_list, b_list):
        for va, vb in zip(a, b):
            if isinstance(va, Decimal) and isinstance(vb, Decimal):
                if abs(va - vb) > TOLERANCE:
                    return False
            elif va != vb:
                return False
    return True


def _rows_equal_strict(ref_rows, cand_rows, ref_sql) -> bool:
    """Comparaison N3 strict :
    - Noms de colonnes doivent correspondre
    - Ordre des lignes compte si ORDER BY dans la référence
    - Arrondi à 3 décimales
    """
    ordered = _reference_has_order_by(ref_sql)
    return _rows_equal(ref_rows, cand_rows, ordered=ordered)


# ---------------------------------------------------------------------------
# Exécution de la reference_sql
# ---------------------------------------------------------------------------

def run_reference_sql(sql_template: str, target: str) -> tuple[bool, list[dict], str]:
    """Exécute reference_sql après substitution du schéma ET via search_path."""
    # Supporter les deux modes : {schema} substitué (anciennes questions) ou search_path
    sql = sql_template.replace("{schema}", target)
    conn = connect_readonly()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {target}")
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return True, rows, ""
    except Exception as e:
        conn.rollback()
        return False, [], str(e)
    finally:
        conn.close()


def run_candidate_sql(sql: str, target: str) -> tuple[bool, list[dict], str]:
    """Exécute la requête candidate avec search_path."""
    conn = connect_readonly()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {target}")
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return True, rows, ""
    except Exception as e:
        conn.rollback()
        return False, [], str(e)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Évaluation d'un run
# ---------------------------------------------------------------------------

def evaluate_run(run_dir: Path) -> tuple[dict, list[dict]]:
    run_dir = run_dir if run_dir.is_absolute() else ROOT / run_dir
    info = json.loads((run_dir / "run_info.json").read_text(encoding="utf-8"))
    results = list(csv.DictReader(open(run_dir / "results.csv", encoding="utf-8")))
    target = info["target_schema"]
    q_set = info["questions_set"]

    q_path = ROOT / "data" / "questions" / f"questions_{q_set}.json"
    questions = {q["id"]: q for q in json.loads(q_path.read_text(encoding="utf-8"))["questions"]}

    details = []
    for r in results:
        qid = r["question_id"]
        q = questions.get(qid, {})
        ref_sql = q.get("reference_sql", "")

        # N1 : syntaxe
        sql_candidate = r.get("sql_raw_extracted", "")
        sql_strict = str(r.get("sql_strict", "False")).lower() == "true"
        sql_lenient = str(r.get("sql_lenient", "False")).lower() == "true"
        n1_valid = False
        if sql_candidate:
            try:
                trees = sqlglot.parse(sql_candidate, dialect="postgres")
                n1_valid = bool(trees and trees[0] is not None)
            except Exception:
                n1_valid = False

        # N2 : garde
        n2_guard = str(r.get("guard_accepted", "False")).lower() == "true"
        guard_reason = r.get("guard_reason", "")

        # N3 : sémantique (uniquement si garde OK et exécution OK dans le run)
        exec_ok = str(r.get("execution_ok", "False")).lower() == "true"
        n3_strict = None
        n3_loose = None
        n3_truth_strict = None
        n3_truth_loose = None
        ref_target_rows: list[dict] = []
        ref_gold_rows: list[dict] = []
        cand_rows: list[dict] = []

        if n2_guard and exec_ok and ref_sql:
            sql_exec = r.get("sql_executed", "")

            # Reference sur le schéma cible (n3_sql)
            ref_ok, ref_target_rows, _ = run_reference_sql(ref_sql, target)

            # Reference sur gold (n3_truth) — toujours gold même si target=pipeline
            ref_gold_ok, ref_gold_rows, _ = run_reference_sql(ref_sql, "gold")

            # Candidat sur le schéma cible
            if sql_exec and ref_ok:
                cand_ok, cand_rows, _ = run_candidate_sql(sql_exec, target)
                if cand_ok:
                    # n3_sql : candidat vs référence sur le même schéma
                    n3_strict = _rows_equal_strict(ref_target_rows, cand_rows, ref_sql)
                    n3_loose = _rows_equal_loose(ref_target_rows, cand_rows, ref_sql)
                    # n3_truth : candidat vs référence sur gold
                    if ref_gold_ok:
                        n3_truth_strict = _rows_equal_strict(ref_gold_rows, cand_rows, ref_sql)
                        n3_truth_loose = _rows_equal_loose(ref_gold_rows, cand_rows, ref_sql)
                    else:
                        n3_truth_strict = False
                        n3_truth_loose = False
                else:
                    n3_strict = False
                    n3_loose = False
                    n3_truth_strict = False
                    n3_truth_loose = False
            else:
                n3_strict = False
                n3_loose = False
                n3_truth_strict = False
                n3_truth_loose = False

        # Nombres : deux mesures
        answer_raw = r.get("answer_raw", "")
        has_answer = bool(answer_raw and answer_raw != "llm_timeout")
        n_with_answer = 1 if has_answer else 0

        # numbers_faithful : nombres cités dans les lignes renvoyées (le LLM n'invente pas)
        if has_answer and cand_rows:
            _, unverified_faithful = _verify_numbers(answer_raw, cand_rows, r.get("question", ""))
            numbers_faithful = not bool(unverified_faithful)
            nums_faithful_str = "|".join(unverified_faithful)
        else:
            numbers_faithful = None
            nums_faithful_str = ""

        # numbers_true : nombres cités vs valeurs de la référence sur gold
        if has_answer and ref_gold_rows:
            _, unverified_true = _verify_numbers(answer_raw, ref_gold_rows, r.get("question", ""))
            numbers_true = not bool(unverified_true)
            nums_true_str = "|".join(unverified_true)
        else:
            numbers_true = None
            nums_true_str = ""

        details.append({
            "question_id": qid,
            "question": r.get("question", ""),
            "category": q.get("category", ""),
            "sensitive_to": "|".join(q.get("sensitive_to", [])),
            "sql_strict": sql_strict,
            "sql_lenient": sql_lenient,
            "n1_syntax_valid": n1_valid,
            "n2_guard_accepted": n2_guard,
            "guard_reason": guard_reason,
            "n3_sql_strict": n3_strict,
            "n3_sql_loose": n3_loose,
            "n3_truth_strict": n3_truth_strict,
            "n3_truth_loose": n3_truth_loose,
            "execution_ok": exec_ok,
            "has_answer": has_answer,
            "n_rows_candidate": int(r.get("n_rows", 0) or 0),
            "n_rows_reference": len(ref_target_rows),
            "numbers_faithful": numbers_faithful,
            "numbers_true": numbers_true,
            "nums_faithful_unverified": nums_faithful_str,
            "nums_true_unverified": nums_true_str,
            "errors": r.get("errors", ""),
        })

    n = len(details)

    def rate(num, den):
        return round(num / den, 3) if den else None

    n1_strict = sum(d["sql_strict"] for d in details)
    n1_lenient = sum(d["sql_lenient"] for d in details)
    n1 = sum(d["n1_syntax_valid"] for d in details)
    n2 = sum(d["n2_guard_accepted"] for d in details)
    n3_strict_valid = [d for d in details if d["n3_sql_strict"] is not None]
    n3_sql_strict_true = sum(d["n3_sql_strict"] is True for d in details)
    n3_sql_loose_true = sum(d["n3_sql_loose"] is True for d in details)
    n3_truth_strict_true = sum(d["n3_truth_strict"] is True for d in details)
    n3_truth_loose_true = sum(d["n3_truth_loose"] is True for d in details)
    n_answered = sum(1 for d in details if d["has_answer"])
    n_faithful = sum(1 for d in details if d["numbers_faithful"] is True)
    n_true = sum(1 for d in details if d["numbers_true"] is True)

    summary = {
        "run": info["run"],
        "prompt_sql": info["prompt_sql"],
        "prompt_answer": info["prompt_answer"],
        "model": info["model"],
        "questions_set": q_set,
        "target_schema": target,
        "n_questions": n,
        "n_answered": n_answered,
        "sql_strict_rate": rate(n1_strict, n),
        "sql_lenient_rate": rate(n1_lenient, n),
        "n1_syntax_rate": rate(n1, n),
        "n2_guard_rate": rate(n2, n),
        "n3_sql_strict_rate": rate(n3_sql_strict_true, n),
        "n3_sql_loose_rate": rate(n3_sql_loose_true, n),
        "n3_truth_strict_rate": rate(n3_truth_strict_true, n),
        "n3_truth_loose_rate": rate(n3_truth_loose_true, n),
        "numbers_faithful_rate": rate(n_faithful, n_answered),
        "numbers_true_rate": rate(n_true, n_answered),
        "by_category": _group_stats(details, "category"),
        "by_sensitive_to": _group_stats_multi(details, "sensitive_to"),
    }

    # Écriture fichiers
    with open(run_dir / "eval_sql.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(details[0].keys()))
        w.writeheader()
        w.writerows(details)
    (run_dir / "eval_sql_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return summary, details


def _group_stats(details: list[dict], key: str) -> dict:
    groups: dict[str, list] = {}
    for d in details:
        groups.setdefault(d[key], []).append(d["n3_sql_loose"])
    result = {}
    for k, vals in sorted(groups.items()):
        n_total = len(vals)
        n_true = sum(v is True for v in vals)
        result[k] = f"{n_true}/{n_total}"
    return result


def _group_stats_multi(details: list[dict], key: str) -> dict:
    """Statistiques par étiquette sensitive_to (n3_sql_loose + n3_truth_loose)."""
    groups: dict[str, dict] = {}
    for d in details:
        labels = [l.strip() for l in d[key].split("|") if l.strip()]
        for label in labels:
            if label not in groups:
                groups[label] = {"sql": [], "truth": []}
            groups[label]["sql"].append(d["n3_sql_loose"])
            groups[label]["truth"].append(d["n3_truth_loose"])
    result = {}
    for k, vals in sorted(groups.items()):
        sql_ok = sum(v is True for v in vals["sql"])
        truth_ok = sum(v is True for v in vals["truth"])
        result[k] = f"sql={sql_ok}/{len(vals['sql'])} truth={truth_ok}/{len(vals['truth'])}"
    return result


# ---------------------------------------------------------------------------
# Mode propagation (C5)
# ---------------------------------------------------------------------------

def evaluate_propagation(run_gold: Path, run_pipeline: Path):
    """Compare les résultats sur gold vs pipeline pour les mêmes questions."""
    run_gold = run_gold if run_gold.is_absolute() else ROOT / run_gold
    run_pipeline = run_pipeline if run_pipeline.is_absolute() else ROOT / run_pipeline

    print(f"\n{'='*60}")
    print(f"PROPAGATION DES ERREURS D'EXTRACTION")
    print(f"  Gold     : {run_gold.name}")
    print(f"  Pipeline : {run_pipeline.name}")
    print(f"{'='*60}")

    sum_g, det_g = evaluate_run(run_gold)
    sum_p, det_p = evaluate_run(run_pipeline)

    ids_g = {d["question_id"]: d for d in det_g}
    ids_p = {d["question_id"]: d for d in det_p}
    common = sorted(set(ids_g) & set(ids_p))

    changed = []
    for qid in common:
        g = ids_g[qid]
        p = ids_p[qid]
        g_ok = g["n3_truth_loose"]
        p_ok = p["n3_truth_loose"]
        if g_ok != p_ok:
            changed.append({
                "question_id": qid,
                "question": g["question"],
                "sensitive_to": g["sensitive_to"],
                "gold_n3_truth_loose": g_ok,
                "pipeline_n3_truth_loose": p_ok,
            })

    pct = lambda x: "—" if x is None else f"{x*100:.1f}%"
    print(f"\n  N3 truth souple gold     : {pct(sum_g['n3_truth_loose_rate'])}")
    print(f"  N3 truth souple pipeline : {pct(sum_p['n3_truth_loose_rate'])}")
    print(f"\n  Questions dont la reponse change (n3_truth) entre gold et pipeline : "
          f"{len(changed)}/{len(common)}")

    for c in changed:
        print(f"    {c['question_id']} [{c['sensitive_to']}] "
              f"gold={'OK' if c['gold_n3_truth_loose'] else 'KO'} "
              f"pipeline={'OK' if c['pipeline_n3_truth_loose'] else 'KO'}")
        print(f"      {c['question']}")

    prop_path = ROOT / "runs" / "propagation_comparison.json"
    prop = {
        "run_gold": run_gold.name,
        "run_pipeline": run_pipeline.name,
        "n_common": len(common),
        "n_changed": len(changed),
        "n3_truth_loose_gold": sum_g["n3_truth_loose_rate"],
        "n3_truth_loose_pipeline": sum_p["n3_truth_loose_rate"],
        "n3_sql_loose_gold": sum_g["n3_sql_loose_rate"],
        "n3_sql_loose_pipeline": sum_p["n3_sql_loose_rate"],
        "changed_questions": changed,
    }
    prop_path.write_text(json.dumps(prop, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Résultats propagation : {prop_path}")
    return prop


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Évaluation Text-to-SQL")
    parser.add_argument("runs", nargs="+",
                        help="dossier(s) de run text2sql")
    parser.add_argument("--propagation", action="store_true",
                        help="mode propagation : comparer gold (arg1) vs pipeline (arg2)")
    args = parser.parse_args()

    if args.propagation:
        if len(args.runs) != 2:
            raise SystemExit("--propagation attend exactement 2 dossiers : gold pipeline")
        evaluate_propagation(Path(args.runs[0]), Path(args.runs[1]))
        return

    for run_arg in args.runs:
        run_dir = Path(run_arg)
        summary, details = evaluate_run(run_dir)
        _print_summary(summary, details)

    print(f"\nFichiers écrits : eval_sql.csv, eval_sql_summary.json dans chaque run.")


def _print_summary(s: dict, details: list[dict]):
    pct = lambda x: "—" if x is None else f"{x*100:5.1f}%"
    print(f"\n=== {s['run']}")
    print(f"    {s['prompt_sql']} · {s['model']} · {s['questions_set']} · "
          f"schema {s['target_schema']} · {s['n_questions']} questions "
          f"({s['n_answered']} avec reponse)")
    print(f"  SQL strict / lenient  : {pct(s.get('sql_strict_rate'))} / {pct(s.get('sql_lenient_rate'))}")
    print(f"  N1 syntaxe valide     : {pct(s['n1_syntax_rate'])}")
    print(f"  N2 garde acceptee    : {pct(s['n2_guard_rate'])}")
    print(f"  N3 SQL strict         : {pct(s['n3_sql_strict_rate'])}")
    print(f"  N3 SQL souple         : {pct(s['n3_sql_loose_rate'])}")
    print(f"  N3 truth strict       : {pct(s['n3_truth_strict_rate'])}")
    print(f"  N3 truth souple       : {pct(s['n3_truth_loose_rate'])}")
    print(f"  Nombres fideles       : {pct(s.get('numbers_faithful_rate'))} ({s.get('n_answered','?')} reponses)")
    print(f"  Nombres vrais         : {pct(s.get('numbers_true_rate'))} ({s.get('n_answered','?')} reponses)")
    print(f"  Par categorie (N3 SQL souple) : {s['by_category']}")
    print(f"  Par sensitive_to      : {s['by_sensitive_to']}")

    wrong = [d for d in details if d["n3_sql_loose"] is False]
    if wrong:
        print("  Erreurs N3 SQL souple :")
        for d in wrong:
            print(f"    {d['question_id']} [{d['category']}] {d['question'][:60]}")


if __name__ == "__main__":
    main()
