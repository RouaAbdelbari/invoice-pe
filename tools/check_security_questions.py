"""
tools/check_security_questions.py
Teste les 8 questions de questions_security.json via sql_v0 sur gold.
Pour chaque question, montre le SQL généré par le LLM et la décision sql_guard.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm import call_llm
from sql_guard import check as guard_check, ALLOWED_TABLES

TABLES_LIST = ", ".join(sorted(ALLOWED_TABLES))

def main():
    tmpl = (ROOT / "prompts" / "sql_v0.txt").read_text(encoding="utf-8")
    qs = json.loads((ROOT / "data" / "questions" / "questions_security.json")
                    .read_text(encoding="utf-8"))["questions"]

    print(f"{'='*65}")
    print(f"Test des 8 questions de sécurité — sql_v0 → sql_guard")
    print(f"{'='*65}\n")

    all_blocked = True
    for q in qs:
        qid   = q["id"]
        qtxt  = q["question"]
        expected_reason = q.get("expected_guard_reason", "")

        prompt = tmpl.replace("{tables}", TABLES_LIST).replace("{question}", qtxt)
        result = call_llm(prompt)

        if result.timed_out:
            print(f"[{qid}] TIMEOUT après {result.elapsed}s")
            continue

        raw_sql = result.text.strip()
        # Extraction rapide
        import re
        m = re.search(r"```(?:sql)?\s*\n?(.*?)```", raw_sql, re.DOTALL | re.IGNORECASE)
        if m:
            sql_extracted = m.group(1).strip()
        else:
            m2 = re.search(r"((?:WITH|SELECT|DELETE|DROP|UPDATE|INSERT|TRUNCATE|COPY)\b.*)",
                           raw_sql, re.DOTALL | re.IGNORECASE)
            sql_extracted = m2.group(1).strip() if m2 else raw_sql

        accepted, rewritten, reason = guard_check(sql_extracted)

        status = "BLOQUEE ✓" if not accepted else "ACCEPTEE ⚠"
        if accepted:
            all_blocked = False

        print(f"[{qid}] {qtxt[:60]}")
        print(f"  SQL généré  : {sql_extracted[:80].replace(chr(10),' ')}")
        print(f"  Garde       : {status}")
        print(f"  Motif       : {reason}")
        print(f"  Attendu     : {expected_reason}")
        print()

    print(f"{'='*65}")
    if all_blocked:
        print("RESULTAT : toutes les questions malveillantes ont été bloquées ✓")
    else:
        print("RESULTAT : au moins une question malveillante a passé la garde ⚠")

if __name__ == "__main__":
    main()
