"""compare_sql_v0_v1.py — McNemar v0 vs v1 sur N3 et exec_ok."""
import json, sys
from pathlib import Path
import scipy.stats as stats

sys.path.insert(0, "src")
from evaluate_sql import evaluate_run

runs = {
    "v0_gold":   "runs/20261105_122150_sql_v0_sql_dev_gold",
    "v1_gold":   "runs/20261105_140848_sql_v1_sql_dev_gold",
    "v0_pipeline": "runs/20261105_134045_sql_v0_sql_dev_pipeline",
    "v1_pipeline": "runs/20261105_141530_sql_v1_sql_dev_pipeline",
}
details = {}
for k, r in runs.items():
    s, d = evaluate_run(Path(r))
    details[k] = {x["question_id"]: x for x in d}

qids = sorted(details["v0_gold"].keys())

# McNemar N3
a = [details["v0_gold"][q]["n3_semantic_correct"] is True for q in qids]
b = [details["v1_gold"][q]["n3_semantic_correct"] is True for q in qids]
n11 = sum(x and y for x,y in zip(a,b))
n10 = sum(x and not y for x,y in zip(a,b))
n01 = sum(not x and y for x,y in zip(a,b))
n00 = sum(not x and not y for x,y in zip(a,b))
disc = n10 + n01
p = stats.binomtest(min(n10,n01), disc, p=0.5, alternative="two-sided").pvalue if disc else 1.0

print("=== McNemar N3 : v0_gold vs v1_gold ===")
print(f"              v1=OK   v1=KO")
print(f"  v0=OK        {n11:>4}     {n10:>4}")
print(f"  v0=KO        {n01:>4}     {n00:>4}")
print(f"  Discordants: {disc}  p-value: {p:.4f}")
print()

# McNemar exec_ok
a2 = [details["v0_gold"][q]["execution_ok"] for q in qids]
b2 = [details["v1_gold"][q]["execution_ok"] for q in qids]
n11e = sum(x and y for x,y in zip(a2,b2))
n10e = sum(x and not y for x,y in zip(a2,b2))
n01e = sum(not x and y for x,y in zip(a2,b2))
n00e = sum(not x and not y for x,y in zip(a2,b2))
disce = n10e + n01e
pe = stats.binomtest(min(n10e,n01e), disce, p=0.5, alternative="two-sided").pvalue if disce else 1.0
print("=== McNemar exec_ok : v0_gold vs v1_gold ===")
print(f"              v1=OK   v1=KO")
print(f"  v0=OK        {n11e:>4}     {n10e:>4}")
print(f"  v0=KO        {n01e:>4}     {n00e:>4}")
print(f"  Discordants: {disce}  p-value: {pe:.4f}")
print()

# Tableau comparatif
print("=== Comparatif ===")
hdr = f"{'Metrique':<25} {'v0_gold':>10} {'v1_gold':>10} {'v0_pipe':>10} {'v1_pipe':>10}"
print(hdr)
for m in ["n1_syntax_valid","n2_guard_accepted","execution_ok","n3_semantic_correct","numbers_ok"]:
    def safe_sum(d, q, m):
        v = d[q][m]
        if v is None: return 0
        if isinstance(v, bool): return int(v)
        if isinstance(v, str): return 0
        return int(v)
    v0g = sum(safe_sum(details["v0_gold"], q, m) for q in qids) / len(qids)
    v1g = sum(safe_sum(details["v1_gold"], q, m) for q in qids) / len(qids)
    v0p = sum(safe_sum(details["v0_pipeline"], q, m) for q in qids) / len(qids)
    v1p = sum(safe_sum(details["v1_pipeline"], q, m) for q in qids) / len(qids)
    print(f"{m:<25} {v0g*100:>9.0f}% {v1g*100:>9.0f}% {v0p*100:>9.0f}% {v1p*100:>9.0f}%")

print()
print(f"{'Q':<10} {'v0_N3':>6} {'v1_N3':>6} {'v0_exec':>7} {'v1_exec':>7}")
for q in qids:
    v0n3 = str(details["v0_gold"][q]["n3_semantic_correct"])
    v1n3 = str(details["v1_gold"][q]["n3_semantic_correct"])
    v0ex = str(details["v0_gold"][q]["execution_ok"])
    v1ex = str(details["v1_gold"][q]["execution_ok"])
    print(f"{q:<10} {v0n3:>6} {v1n3:>6} {v0ex:>7} {v1ex:>7}")
