"""
tools/audit_n3.py — Audit détaillé de N3 pour un run text2sql.

Pour chaque question :
  1. Affiche reference_sql et sql candidat
  2. Exécute les deux sur le même schéma
  3. Affiche colonnes + 5 premières lignes de chaque résultat
  4. Classifie l'échec (ou le succès) dans une catégorie

Analyse aussi les numbers_unverified (WARN).

Usage :
    python tools\\audit_n3.py runs\\<run_sql_v1_gold>
"""

import csv
import json
import sys
from decimal import Decimal
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from db import connect_readonly


def run_query(sql: str, target: str) -> tuple[list[str], list[tuple]]:
    conn = connect_readonly()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {target}")
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return cols, rows
    except Exception as e:
        conn.rollback()
        return [], [("ERROR", str(e)[:200])]
    finally:
        conn.close()


def fmt_val(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def rows_to_str(cols, rows, max_rows=5) -> str:
    if not cols:
        return "  (pas de colonnes)"
    lines = [f"  Colonnes : {cols}"]
    for i, r in enumerate(rows[:max_rows]):
        lines.append(f"  [{i+1}] {', '.join(fmt_val(v) for v in r)}")
    if len(rows) > max_rows:
        lines.append(f"  ... ({len(rows)} lignes au total)")
    elif len(rows) == 0:
        lines.append("  (aucune ligne)")
    return "\n".join(lines)


def compare_results(ref_cols, ref_rows, cand_cols, cand_rows) -> str:
    """Retourne une classification du résultat."""
    if not ref_cols and not cand_cols:
        return "ERREUR_DEUX"

    # Vérifier s'il y a une erreur d'exécution
    if cand_cols == [] and len(cand_rows) == 1 and isinstance(cand_rows[0][0], str) and cand_rows[0][0] == "ERROR":
        return f"ERREUR_EXEC_CANDIDAT: {cand_rows[0][1][:80]}"

    # Succès : mêmes valeurs ?
    if ref_rows == cand_rows and ref_cols == cand_cols:
        return "OK_EXACT"

    # Même nombre de lignes ?
    if len(ref_rows) != len(cand_rows):
        return f"vraie_erreur (lignes: ref={len(ref_rows)} vs cand={len(cand_rows)})"

    # Mêmes valeurs mais colonnes différentes ?
    if len(ref_cols) != len(cand_cols):
        # Vérifier si les valeurs sont les mêmes dans le même ordre
        same_vals = True
        for r1, r2 in zip(ref_rows, cand_rows):
            # Comparer en ignorant les colonnes en plus
            min_len = min(len(r1), len(r2))
            if list(r1[:min_len]) != list(r2[:min_len]):
                same_vals = False
                break
        if same_vals and len(cand_cols) > len(ref_cols):
            return "colonnes_en_plus"
        return f"vraie_erreur (nb colonnes: ref={len(ref_cols)} vs cand={len(cand_cols)})"

    # Même nombre de colonnes, vérifier valeurs
    all_match = True
    type_mismatch = False
    for r1, r2 in zip(ref_rows, cand_rows):
        for v1, v2 in zip(r1, r2):
            if v1 == v2:
                continue
            # Vérifier si c'est juste un problème de type (Decimal vs float)
            try:
                if float(v1) == float(v2):
                    type_mismatch = True
                    continue
            except (TypeError, ValueError):
                pass
            all_match = False
            break
        if not all_match:
            break

    if all_match and type_mismatch:
        return "format_numerique"

    if all_match and ref_cols != cand_cols:
        # Mêmes valeurs mais noms de colonnes différents
        return "alias_ou_noms_colonnes"

    # Vérifier si c'est juste l'ordre des lignes
    if sorted([tuple(r) for r in ref_rows]) == sorted([tuple(r) for r in cand_rows]):
        if ref_cols == cand_cols:
            return "ordre_lignes"
        return "ordre_lignes + alias_ou_noms_colonnes"

    # Vérifier si les valeurs sont les mêmes mais dans un ordre de colonnes différent
    ref_sorted = sorted([tuple(sorted(r)) for r in ref_rows])
    cand_sorted = sorted([tuple(sorted(r)) for r in cand_rows])
    if ref_sorted == cand_sorted and ref_cols != cand_cols:
        return "ordre_colonnes"

    return "vraie_erreur"


def audit_run(run_dir: str):
    run_path = Path(run_dir)
    if not run_path.is_absolute():
        run_path = ROOT / run_path

    info = json.loads((run_path / "run_info.json").read_text(encoding="utf-8"))
    target = info["target_schema"]
    q_set = info["questions_set"]
    results = list(csv.DictReader(open(run_path / "results.csv", encoding="utf-8")))

    q_path = ROOT / "data" / "questions" / f"questions_{q_set}.json"
    questions = {q["id"]: q for q in json.loads(q_path.read_text(encoding="utf-8"))["questions"]}

    print(f"{'='*70}")
    print(f"AUDIT N3 — {run_path.name}")
    print(f"  Schéma : {target} · Questions : {q_set} · {len(results)} questions")
    print(f"{'='*70}")

    for r in results:
        qid = r["question_id"]
        q = questions.get(qid, {})
        ref_sql = q.get("reference_sql", "").replace("{schema}", target)
        cand_sql = r.get("sql_executed", "")
        exec_ok = str(r.get("execution_ok", "False")).lower() == "true"
        n3 = r.get("n3_semantic_correct", "None")
        nums_unverified = r.get("numbers_unverified", "")

        print(f"\n{'─'*70}")
        print(f"[{qid}] {q.get('question', '')}")
        print(f"  Catégorie : {q.get('category', '')} | sensitive_to : {q.get('sensitive_to', '')}")
        print(f"  N3 dans eval : {n3} | exec_ok : {exec_ok}")
        if nums_unverified:
            print(f"  numbers_unverified : {nums_unverified}")
        print()

        print(f"  --- reference_sql ---")
        print(f"  {ref_sql}")
        print()

        print(f"  --- sql candidat (exécuté) ---")
        print(f"  {cand_sql}")
        print()

        # Exécuter les deux
        ref_cols, ref_rows = run_query(ref_sql, target)
        if exec_ok and cand_sql:
            cand_cols, cand_rows = run_query(cand_sql, target)
        else:
            cand_cols, cand_rows = [], [("NON_EXECUTE", r.get("execution_error", "")[:100])]

        print(f"  --- Résultat référence ---")
        print(rows_to_str(ref_cols, ref_rows))
        print()

        print(f"  --- Résultat candidat ---")
        print(rows_to_str(cand_cols, cand_rows))
        print()

        # Classification
        classification = compare_results(ref_cols, ref_rows, cand_cols, cand_rows)
        print(f"  >>> CLASSIFICATION : {classification}")

        # Analyse des nombres non vérifiés
        if nums_unverified:
            print(f"  --- Analyse numbers_unverified ---")
            answer = r.get("answer_raw", "")
            print(f"  Réponse LLM : {answer[:200]}")
            print(f"  Nombres cités : {r.get('numbers_cited', '')}")
            print(f"  Non vérifiés : {nums_unverified}")
            # Montrer les valeurs des lignes de référence
            all_ref_vals = set()
            for row in ref_rows:
                for v in row:
                    all_ref_vals.add(fmt_val(v))
            print(f"  Valeurs dans référence : {sorted(all_ref_vals)[:20]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python tools\\audit_n3.py runs\\<run>")
        sys.exit(1)
    audit_run(sys.argv[1])
