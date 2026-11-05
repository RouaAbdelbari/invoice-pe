"""
text2sql.py — Étape 2 : Text-to-SQL (Phase C3)

Pour chaque question d'un fichier questions_<set>.json :
  1. Construit le prompt SQL ({tables} ou {schema} + {question})
  2. Appelle le LLM (timeout 180 s) → extrait SQL (strict / lenient)
  3. Passe par sql_guard
  4. Exécute sous bi_readonly avec SET search_path TO <target>
  5. Construit le prompt de réponse (50 lignes max + "(N lignes au total)")
  6. Appelle le LLM (timeout 180 s) → réponse française
  7. Vérifie les nombres cités
  8. Enregistre dans runs/<date>_<prompt_sql>_sql_<questions>_<target>/

Timings enregistrés par question : t_llm_sql, t_guard, t_db, t_llm_answer.

Marqueurs de prompt :
  {tables}  : noms de tables sans schéma (sql_v0)
  {schema}  : DDL complet (sql_v1+)
  {question}: texte de la question
  {rows}    : résultats JSON tronqués à 50 lignes (prompt de réponse)

Utilisation :
    python src\\text2sql.py --questions dev --prompt-sql sql_v0 --prompt-answer answer_v0 --target gold
    python src\\text2sql.py --questions dev --prompt-sql sql_v0 --prompt-answer answer_v0 --target pipeline
"""

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import connect_readonly
from llm import call_llm as _llm_call, MODEL_OPTIONS, TIMEOUT_SENTINEL
from sql_guard import check as guard_check, ALLOWED_TABLES

MAX_ROWS_IN_PROMPT = 50   # lignes de résultats transmises au prompt de réponse

TABLES_LIST = ", ".join(sorted(ALLOWED_TABLES))

RESULT_FIELDS = [
    "question_id", "question",
    "t_llm_sql", "t_guard", "t_db", "t_llm_answer",
    "sql_strict", "sql_lenient",
    "sql_raw_extracted",
    "guard_accepted", "guard_reason",
    "sql_executed",
    "execution_ok", "execution_error", "n_rows",
    "answer_raw",
    "numbers_cited", "numbers_unverified",
    "errors",
]


# ---------------------------------------------------------------------------
# Chargement des prompts
# ---------------------------------------------------------------------------

def load_prompt_sql(name: str) -> tuple[str, str]:
    path = ROOT / "prompts" / f"{name}.txt"
    if not path.exists():
        raise SystemExit(f"Prompt SQL introuvable : {path}")
    text = path.read_text(encoding="utf-8")
    if "{question}" not in text:
        raise SystemExit(f"{path} doit contenir {{question}}")
    if "{tables}" not in text and "{schema}" not in text:
        raise SystemExit(f"{path} doit contenir {{tables}} ou {{schema}}")
    return text, hashlib.sha256(text.encode()).hexdigest()[:12]


def load_prompt_answer(name: str) -> tuple[str, str]:
    path = ROOT / "prompts" / f"{name}.txt"
    if not path.exists():
        raise SystemExit(f"Prompt réponse introuvable : {path}")
    text = path.read_text(encoding="utf-8")
    for m in ("{question}", "{rows}"):
        if m not in text:
            raise SystemExit(f"{path} doit contenir {m}")
    return text, hashlib.sha256(text.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# DDL complet pour {schema}
# ---------------------------------------------------------------------------

def build_schema_description(target: str) -> str:
    s = target
    return """\
Tables disponibles (sans qualification de schéma) :

fact_invoice — une ligne par facture validée
  invoice_key    INTEGER       clé surrogate (PK, auto-incrémentée)
  invoice_id     TEXT          identifiant source (ex. "dev_001")
  invoice_number TEXT          numéro imprimé sur la facture (ex. "D-2025-0001")
  supplier_id    INTEGER       FK → dim_supplier.supplier_id
  date_id        INTEGER       FK → dim_date.date_id (format AAAAMMJJ, ex. 20250726)
  vat_id         INTEGER       FK → dim_vat.vat_id
  net_amount     NUMERIC(14,3) montant hors taxes en TND, 3 décimales (ex. 2988.529)
  vat_amount     NUMERIC(14,3) montant de la TVA en TND, 3 décimales (ex. 567.821)
  total_amount   NUMERIC(14,3) montant TTC en TND, 3 décimales (ex. 3556.350)
  is_valid       BOOLEAN       toujours TRUE dans cette table

dim_supplier — un fournisseur par ligne
  supplier_id    INTEGER       PK
  supplier_name  TEXT          nom exact du fournisseur (ex. "Ayari Import SA")

dim_date — une date par ligne
  date_id        INTEGER       PK, format AAAAMMJJ (ex. 20250726)
  full_date      DATE          date complète (ex. 2025-07-26)
  year           SMALLINT      année (ex. 2025)
  quarter        SMALLINT      trimestre 1 à 4 (T3 = juillet-septembre)
  month          SMALLINT      mois 1 à 12
  month_name     TEXT          nom du mois en français, majuscule initiale et accents :
                               'Janvier', 'Fevrier', 'Mars', 'Avril', 'Mai', 'Juin',
                               'Juillet', 'Aout', 'Septembre', 'Octobre', 'Novembre', 'Decembre'

dim_vat — un taux de TVA par ligne
  vat_id         INTEGER       PK
  vat_rate       NUMERIC(5,4)  taux décimal (ex. 0.0700 = 7 %, 0.1300 = 13 %, 0.1900 = 19 %)

Jointures :
  fact_invoice JOIN dim_supplier USING (supplier_id)
  fact_invoice JOIN dim_date     USING (date_id)
  fact_invoice JOIN dim_vat      USING (vat_id)

Exemples de valeurs dim_vat  : vat_rate in (0.0700, 0.1300, 0.1900)
Exemples de valeurs dim_date : date_id=20250712, quarter=3, month=7, month_name='Juillet'
                               date_id=20250901, quarter=3, month=9, month_name='Septembre'"""


# ---------------------------------------------------------------------------
# Extraction SQL
# ---------------------------------------------------------------------------

def extract_sql(raw: str) -> tuple[str, bool, bool]:
    """Retourne (sql, strict, lenient)."""
    r = raw.strip()
    upper = r.upper()
    if upper.startswith("SELECT") or upper.startswith("WITH"):
        return r, True, True
    m = re.search(r"```(?:sql)?\s*\n?(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip(), False, True
    m = re.search(r"((?:WITH|SELECT)\b.*)", raw, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip(), False, True
    return r, False, False


# ---------------------------------------------------------------------------
# Troncature des résultats pour le prompt de réponse
# ---------------------------------------------------------------------------

def truncate_rows(rows: list[dict], max_rows: int = MAX_ROWS_IN_PROMPT) -> str:
    """Sérialise en JSON les N premières lignes + note si tronqué."""
    if len(rows) <= max_rows:
        return json.dumps(rows, ensure_ascii=False, default=str)
    truncated = rows[:max_rows]
    note = f"\n(... {len(rows) - max_rows} lignes supplémentaires non affichées, {len(rows)} lignes au total)"
    return json.dumps(truncated, ensure_ascii=False, default=str) + note


# ---------------------------------------------------------------------------
# Exécution SQL avec search_path
# ---------------------------------------------------------------------------

def execute_sql(sql: str, target: str) -> tuple[bool, list[dict], str, float]:
    """Retourne (ok, rows, error, elapsed_s)."""
    start = time.perf_counter()
    conn = connect_readonly()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {target}")
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return True, rows, "", round(time.perf_counter() - start, 2)
    except Exception as e:
        conn.rollback()
        return False, [], str(e), round(time.perf_counter() - start, 2)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Vérification des nombres
# ---------------------------------------------------------------------------

def _extract_numbers_french_aware(text: str, question: str) -> list[str]:
    """Extrait les nombres en reconnaissant le format français.

    Reconnaît :
      - 537 272,397 (espaces milliers + virgule décimale)
      - 8 696 794,71
      - 184928.481 (point décimal)
      - 17 (entier)

    Ignore :
      - Les années (2024, 2025, 2026)
      - Les numéros de trimestre/mois (1-12, 1-4) s'ils apparaissent isolés
      - Les puces de liste (1., 2), 3.) au début d'une ligne
      - Les nombres déjà présents dans la question
    """
    # Nombres dans la question à ignorer
    q_nums = set(re.findall(r"\b\d{4}\b", question))  # années
    q_all_nums = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", question))

    # Retirer les puces de liste (1., 2), 3.) en début de ligne
    cleaned = re.sub(r"^\s*\d+[.)]\s", "", text, flags=re.MULTILINE)

    # Patterns de nombres avec format français :
    # Groupe de chiffres séparés par espaces + virgule décimale optionnelle
    # Ex: 537 272,397 | 8 696 794,71 | 17 | 29050.692
    patterns = [
        r"\b\d{1,3}(?:\s\d{3})+(?:,\d+)?\b",   # 537 272,397 (espaces milliers)
        r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b",   # 537,272.397 (virgules milliers en)
        r"\b\d+(?:[.,]\d+)?\b",                 # 17 | 29050.692 | 12624,97
    ]

    found = []
    for p in patterns:
        for m in re.finditer(p, cleaned):
            val = m.group()
            # Ignorer les années
            if val in q_nums:
                continue
            # Ignorer les nombres déjà dans la question
            # (comparaison normalisée : sans espaces ni séparateurs)
            val_clean = val.replace(" ", "").replace(",", ".").replace(" ", "")
            q_clean = {n.replace(" ", "").replace(",", ".") for n in q_all_nums}
            if val_clean in q_clean:
                continue
            # Ignorer si déjà trouvé (éviter doublons des patterns qui se chevauchent)
            if val not in found:
                found.append(val)

    return found


def normalize_num(s: str) -> float | None:
    """Convertit un nombre formaté (fr ou en) en float."""
    s = s.strip()
    # Format français : espaces milliers + virgule décimale
    # ex: "537 272,397" -> "537272.397"
    if " " in s and "," in s:
        s = s.replace(" ", "").replace(",", ".")
    # Format français : virgule décimale seule
    # ex: "12624,97" -> "12624.97"
    elif "," in s and "." not in s:
        s = s.replace(" ", "").replace(",", ".")
    # Format anglais : virgules milliers + point décimal
    # ex: "537,272.397" -> "537272.397"
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


def verify_numbers(answer: str, rows: list[dict], question: str = "") -> tuple[list[str], list[str]]:
    """Vérifie que les nombres cités dans la réponse figurent dans les lignes.

    Ignore les années, les nombres de la question, et les puces de liste.
    """
    cited = _extract_numbers_french_aware(answer, question)
    if not cited:
        return [], []
    row_values: set[float] = set()
    for row in rows:
        for v in row.values():
            if v is None:
                continue
            n = normalize_num(str(v))
            if n is not None:
                row_values.add(round(n, 3))
    unverified = [c for c in cited
                  if (n := normalize_num(c)) is not None
                  and not any(abs(n - rv) <= 0.001 for rv in row_values)]
    return cited, unverified


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Text-to-SQL")
    parser.add_argument("--questions", choices=["dev", "test"], required=True)
    parser.add_argument("--prompt-sql", default="sql_v0")
    parser.add_argument("--prompt-answer", default="answer_v0")
    parser.add_argument("--target", choices=["gold", "pipeline"], required=True)
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--pipeline-run", default=None)
    args = parser.parse_args()

    tmpl_sql, sha_sql = load_prompt_sql(args.prompt_sql)
    tmpl_ans, sha_ans = load_prompt_answer(args.prompt_answer)
    uses_tables = "{tables}" in tmpl_sql
    uses_schema = "{schema}" in tmpl_sql
    schema_content = build_schema_description(args.target) if uses_schema else ""

    q_path = ROOT / "data" / "questions" / f"questions_{args.questions}.json"
    if not q_path.exists():
        raise SystemExit(f"Questions introuvables : {q_path}")
    questions = json.loads(q_path.read_text(encoding="utf-8"))["questions"]
    if args.limit:
        questions = questions[: args.limit]

    run_name = (f"{datetime.now():%Y%m%d_%H%M%S}_{args.prompt_sql}_sql"
                f"_{args.questions}_{args.target}")
    run_dir = ROOT / "runs" / run_name
    (run_dir / "raw_sql").mkdir(parents=True)
    (run_dir / "raw_answer").mkdir(parents=True)

    run_info = {
        "run": run_name, "stage": "text2sql",
        "questions_set": args.questions, "target_schema": args.target,
        "prompt_sql": args.prompt_sql, "prompt_sql_sha256_12": sha_sql,
        "prompt_sql_marker": "tables" if uses_tables else "schema",
        "prompt_answer": args.prompt_answer, "prompt_answer_sha256_12": sha_ans,
        "model": args.model, "model_options": MODEL_OPTIONS,
        "n_questions": len(questions), "pipeline_run": args.pipeline_run,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    (run_dir / "run_info.json").write_text(
        json.dumps(run_info, ensure_ascii=False, indent=2), encoding="utf-8")

    marker = "tables" if uses_tables else "schema"
    print(f"Run : {run_name}")
    print(f"  {len(questions)} questions · {args.target} · {args.prompt_sql}/{args.prompt_answer} · marqueur={marker}")

    rows_out = []
    for q in questions:
        qid = q["id"]
        qtxt = q["question"]
        print(f"  {qid} ...", end=" ", flush=True)

        row = {f: "" for f in RESULT_FIELDS}
        row["question_id"] = qid
        row["question"] = qtxt
        errors = []

        # --- LLM SQL ---
        p_sql = tmpl_sql.replace("{question}", qtxt)
        if uses_tables:
            p_sql = p_sql.replace("{tables}", TABLES_LIST)
        if uses_schema:
            p_sql = p_sql.replace("{schema}", schema_content)

        res_sql = _llm_call(p_sql, model=args.model)
        row["t_llm_sql"] = res_sql.elapsed
        (run_dir / "raw_sql" / f"{qid}.txt").write_text(res_sql.text, encoding="utf-8")

        if res_sql.timed_out:
            errors.append("llm_sql_timeout")
            row["errors"] = ";".join(errors)
            rows_out.append(row)
            print(f"LLM_TIMEOUT ({res_sql.elapsed}s)")
            continue

        sql_extracted, sql_strict, sql_lenient = extract_sql(res_sql.text)
        row["sql_strict"] = sql_strict
        row["sql_lenient"] = sql_lenient
        row["sql_raw_extracted"] = sql_extracted
        if not sql_lenient:
            errors.append("sql_not_extracted")

        # --- Garde ---
        t_guard_start = time.perf_counter()
        guard_ok, sql_rewritten, guard_reason = guard_check(sql_extracted)
        row["t_guard"] = round(time.perf_counter() - t_guard_start, 3)
        row["guard_accepted"] = guard_ok
        row["guard_reason"] = guard_reason
        row["sql_executed"] = sql_rewritten or ""

        if not guard_ok:
            errors.append(f"guard:{guard_reason[:60]}")
            row["errors"] = ";".join(errors)
            rows_out.append(row)
            print(f"GARDE ({guard_reason[:50]})")
            continue

        # --- DB ---
        exec_ok, result_rows, exec_error, t_db = execute_sql(sql_rewritten, args.target)
        row["t_db"] = t_db
        row["execution_ok"] = exec_ok
        row["execution_error"] = exec_error
        row["n_rows"] = len(result_rows) if exec_ok else 0

        if not exec_ok:
            errors.append(f"exec:{exec_error[:60]}")
            row["errors"] = ";".join(errors)
            rows_out.append(row)
            print(f"EXEC_ERR  sql={res_sql.elapsed:.1f}s  db={t_db:.2f}s")
            continue

        # --- LLM réponse ---
        rows_text = truncate_rows(result_rows)
        p_ans = tmpl_ans.replace("{question}", qtxt).replace("{rows}", rows_text)

        res_ans = _llm_call(p_ans, model=args.model)
        row["t_llm_answer"] = res_ans.elapsed
        (run_dir / "raw_answer" / f"{qid}.txt").write_text(res_ans.text, encoding="utf-8")

        if res_ans.timed_out:
            errors.append("llm_answer_timeout")
            row["answer_raw"] = TIMEOUT_SENTINEL
        else:
            row["answer_raw"] = res_ans.text.strip()
            cited, unverified = verify_numbers(res_ans.text, result_rows, qtxt)
            row["numbers_cited"] = "|".join(cited)
            row["numbers_unverified"] = "|".join(unverified)
            if unverified:
                errors.append(f"unverified:{','.join(unverified[:3])}")

        row["errors"] = ";".join(errors)
        rows_out.append(row)

        status = "OK" if not errors else "WARN"
        print(f"{status}  sql={res_sql.elapsed:.1f}s  grd={row['t_guard']}s  "
              f"db={t_db:.2f}s  ans={res_ans.elapsed:.1f}s  rows={row['n_rows']}")

    # Écriture résultats
    with open(run_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        w.writeheader()
        w.writerows(rows_out)

    n = len(rows_out)
    n_g  = sum(1 for r in rows_out if str(r["guard_accepted"]).lower() == "true")
    n_ex = sum(1 for r in rows_out if str(r["execution_ok"]).lower() == "true")
    print(f"\nResume : {n} questions · garde={n_g}/{n} · exec={n_ex}/{n}")
    print(f"Dossier : {run_dir}")


if __name__ == "__main__":
    main()
