import csv
from collections import Counter
from pathlib import Path

rows = list(csv.DictReader(open("data/dev2/ground_truth.csv", encoding="utf-8")))
combos = Counter((r["layout"], r["number_format"]) for r in rows)
print("Couverture layout x number_format :")
for k, v in sorted(combos.items()):
    print(f"  {k[0]:15} x {k[1]:6} : {v}")
n_incons = sum(1 for r in rows if r["is_inconsistent"] == "True")
n_anom = sum(1 for r in rows if r["anomaly_type"])
print(f"Total : {len(rows)} factures, {n_incons} incohérentes, {n_anom} anomalies")
