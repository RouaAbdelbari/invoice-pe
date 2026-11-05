"""
tools/check_questions_sql.py — Vérifie que toutes les reference_sql s'exécutent sans erreur.
Usage : python tools\\check_questions_sql.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from db import connect_readonly

conn = connect_readonly()
errors = []
total = 0

for fname in ["data/questions/questions_dev.json", "data/questions/questions_test.json"]:
    data = json.loads((ROOT / fname).read_text(encoding="utf-8"))
    for q in data["questions"]:
        sql = q["reference_sql"].replace("{schema}", "gold")
        total += 1
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.fetchall()
        except Exception as e:
            errors.append(f"{q['id']}: {e}")
            conn.rollback()

conn.close()

if errors:
    print(f"ERREURS ({len(errors)}/{total}) :")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
else:
    print(f"OK — {total} reference_sql executees sans erreur sur gold.")
    sys.exit(0)
