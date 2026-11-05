"""compare_sql_final.py — Tableau v0/v1, gold/pipeline, N3 sql/truth + nombres."""
import json, sys
from pathlib import Path
sys.path.insert(0, "src")
from evaluate_sql import evaluate_run

ROOT = Path(".")
runs = [
    ("v0_gold",     "runs/20261105_122150_sql_v0_sql_dev_gold"),
    ("v1_gold",     "runs/20261105_140848_sql_v1_sql_dev_gold"),
    ("v0_pipeline", "runs/20261105_134045_sql_v0_sql_dev_pipeline"),
    ("v1_pipeline", "runs/20261105_141530_sql_v1_sql_dev_pipeline"),
]

summaries = {}
details = {}
for name, path in runs:
    s, d = evaluate_run(Path(path))
    summaries[name] = s
    details[name] = {x["question_id"]: x for x in d}

print()
print("=" * 80)
print("TABLEAU COMPARATIF — sql_v0 vs sql_v1, gold vs pipeline")
print("Metriques : N3 SQL (qualite SQL), N3 truth (verite), nombres fideles/vrais")
print("=" * 80)
print()

headers = ["Metrique", "v0_gold", "v1_gold", "v0_pipe", "v1_pipe"]
print(f"{headers[0]:<25} {headers[1]:>12} {headers[2]:>12} {headers[3]:>12} {headers[4]:>12}")
print("-" * 80)

qids = sorted(details["v0_gold"].keys())
n = len(qids)

def safe(v):
    if v is True: return 1
    if v is False: return 0
    return 0

metrics = [
    ("exec_ok",        lambda d: sum(1 for q in qids if d[q]["execution_ok"]), n),
    ("N3 SQL strict",   lambda d: sum(safe(d[q].get("n3_sql_strict")) for q in qids), n),
    ("N3 SQL souple",   lambda d: sum(safe(d[q].get("n3_sql_loose")) for q in qids), n),
    ("N3 truth strict", lambda d: sum(safe(d[q].get("n3_truth_strict")) for q in qids), n),
    ("N3 truth souple", lambda d: sum(safe(d[q].get("n3_truth_loose")) for q in qids), n),
    ("nombres fideles", lambda d: sum(1 for q in qids if d[q].get("numbers_faithful") is True), None),
    ("nombres vrais",   lambda d: sum(1 for q in qids if d[q].get("numbers_true") is True), None),
]

for label, fn, denom in metrics:
    vals = []
    for name in ["v0_gold", "v1_gold", "v0_pipeline", "v1_pipeline"]:
        count = fn(details[name])
        d = denom if denom else sum(1 for q in qids if details[name][q].get("has_answer"))
        vals.append(f"{count}/{d}")
    print(f"{label:<25} {vals[0]:>12} {vals[1]:>12} {vals[2]:>12} {vals[3]:>12}")

# Dénominateurs pour les nombres (questions avec réponse)
print()
for name in ["v0_gold", "v1_gold", "v0_pipeline", "v1_pipeline"]:
    n_ans = sum(1 for q in qids if details[name][q].get("has_answer"))
    print(f"  {name}: {n_ans} questions avec reponse (denominateur pour nombres)")

print()
print("Detail par question :")
print(f"{'Q':<10} {'sens':<20} {'v0g_sql':>7} {'v1g_sql':>7} {'v0p_sql':>7} {'v1p_sql':>7} | {'v0g_tru':>7} {'v1g_tru':>7} {'v0p_tru':>7} {'v1p_tru':>7}")
print("-" * 100)
for q in qids:
    sens = details["v0_gold"][q]["sensitive_to"][:18]
    sql_vals = []
    truth_vals = []
    for name in ["v0_gold", "v1_gold", "v0_pipeline", "v1_pipeline"]:
        v = details[name][q].get("n3_sql_loose")
        sql_vals.append("OK" if v is True else "KO" if v is False else "-")
        v2 = details[name][q].get("n3_truth_loose")
        truth_vals.append("OK" if v2 is True else "KO" if v2 is False else "-")
    print(f"{q:<10} {sens:<20} {sql_vals[0]:>7} {sql_vals[1]:>7} {sql_vals[2]:>7} {sql_vals[3]:>7} | {truth_vals[0]:>7} {truth_vals[1]:>7} {truth_vals[2]:>7} {truth_vals[3]:>7}")

print()
print("Nombres par question :")
print(f"{'Q':<10} {'v0g_fid':>7} {'v1g_fid':>7} {'v0p_fid':>7} {'v1p_fid':>7} | {'v0g_tru':>7} {'v1g_tru':>7} {'v0p_tru':>7} {'v1p_tru':>7}")
print("-" * 75)
for q in qids:
    fid_vals = []
    tru_vals = []
    for name in ["v0_gold", "v1_gold", "v0_pipeline", "v1_pipeline"]:
        vf = details[name][q].get("numbers_faithful")
        fid_vals.append("OK" if vf is True else "KO" if vf is False else "-")
        vt = details[name][q].get("numbers_true")
        tru_vals.append("OK" if vt is True else "KO" if vt is False else "-")
    print(f"{q:<10} {fid_vals[0]:>7} {fid_vals[1]:>7} {fid_vals[2]:>7} {fid_vals[3]:>7} | {tru_vals[0]:>7} {tru_vals[1]:>7} {tru_vals[2]:>7} {tru_vals[3]:>7}")
