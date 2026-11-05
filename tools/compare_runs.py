"""compare_runs.py — Compare deux runs d'extraction sur les champs extraits."""
import csv, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
r1 = list(csv.DictReader(open(ROOT / sys.argv[1] / "results.csv", encoding="utf-8")))
r2 = list(csv.DictReader(open(ROOT / sys.argv[2] / "results.csv", encoding="utf-8")))
fields = ["supplier_name","invoice_date","net_amount","vat_amount","total_amount","is_valid"]
identical = True
for a, b in zip(r1, r2):
    for f in fields:
        if a[f] != b[f]:
            print(f"DIFF {a['invoice_id']}.{f}: run1={a[f]} vs run2={b[f]}")
            identical = False
if identical:
    print("DETERMINISTE : les 2 runs sont identiques sur les 5 champs + is_valid")
else:
    print("NON DETERMINISTE : differences detectees")
