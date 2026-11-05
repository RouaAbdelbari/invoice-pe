"""
tests/test_sql_guard.py — Tests unitaires pour src/sql_guard.py

Couvre :
  - Requêtes valides (tables non qualifiées)
  - Refus des tables qualifiées par un schéma (gold.x, pipeline.x, etc.)
  - LIMIT ajouté si absent / conservé si présent
  - DDL/DML bloqués
  - Fonctions dangereuses bloquées
  - pg_catalog / information_schema bloqués
  - Tables hors liste blanche bloquées
  - Plusieurs instructions bloquées
  - Requête vide bloquée
  - 15+ requêtes malveillantes
  - Questions de sécurité questions_security.json
  - Test search_path : même question sur gold vs pipeline interroge deux schémas différents
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sql_guard import check


def accepted(sql: str) -> bool:
    ok, _, _ = check(sql)
    return ok


def refused(sql: str) -> bool:
    return not accepted(sql)


def reason(sql: str) -> str:
    _, _, r = check(sql)
    return r


# ---------------------------------------------------------------------------
# Requêtes valides — tables NON qualifiées (search_path géré par text2sql.py)
# ---------------------------------------------------------------------------

class TestValidQueries:

    def test_simple_select_unqualified(self):
        assert accepted("SELECT COUNT(*) FROM fact_invoice")

    def test_select_with_join_unqualified(self):
        sql = ("SELECT ds.supplier_name, SUM(fi.total_amount) "
               "FROM fact_invoice fi "
               "JOIN dim_supplier ds USING (supplier_id) "
               "GROUP BY ds.supplier_name")
        assert accepted(sql)

    def test_select_with_where_unqualified(self):
        sql = ("SELECT * FROM fact_invoice fi "
               "JOIN dim_date dd USING (date_id) "
               "WHERE dd.year = 2025 AND dd.quarter = 3")
        assert accepted(sql)

    def test_with_cte_unqualified(self):
        sql = ("WITH ranked AS ("
               "  SELECT supplier_id, COUNT(*) AS n "
               "  FROM fact_invoice GROUP BY supplier_id"
               ") SELECT * FROM ranked WHERE n > 1")
        assert accepted(sql)

    def test_all_allowed_tables_unqualified(self):
        for table in ["fact_invoice", "dim_supplier", "dim_date",
                      "dim_vat", "rejected_invoice", "load_info"]:
            assert accepted(f"SELECT * FROM {table}"), f"Devrait être autorisé : {table}"

    def test_rank_window_function(self):
        sql = ("WITH r AS (SELECT supplier_id, COUNT(*) AS n, "
               "RANK() OVER (ORDER BY COUNT(*) DESC) AS rnk "
               "FROM fact_invoice GROUP BY supplier_id) "
               "SELECT * FROM r WHERE rnk = 1")
        assert accepted(sql)


# ---------------------------------------------------------------------------
# Refus des tables qualifiées par un schéma
# ---------------------------------------------------------------------------

class TestSchemaQualifiedRefused:

    def test_gold_qualified_refused(self):
        assert refused("SELECT * FROM gold.fact_invoice")

    def test_pipeline_qualified_refused(self):
        assert refused("SELECT * FROM pipeline.fact_invoice")

    def test_public_qualified_refused(self):
        assert refused("SELECT * FROM public.fact_invoice")

    def test_history_qualified_refused(self):
        assert refused("SELECT * FROM history.invoice_history")

    def test_schema_qualified_reason(self):
        r = reason("SELECT * FROM gold.fact_invoice")
        assert "schema_qualified_table" in r

    def test_pipeline_qualified_reason(self):
        r = reason("SELECT * FROM pipeline.dim_supplier")
        assert "schema_qualified_table" in r


# ---------------------------------------------------------------------------
# LIMIT
# ---------------------------------------------------------------------------

class TestLimitHandling:

    def test_limit_added_when_absent(self):
        ok, rewritten, r = check("SELECT * FROM fact_invoice")
        assert ok
        assert "LIMIT 1000" in rewritten.upper()

    def test_limit_not_duplicated(self):
        ok, rewritten, _ = check("SELECT * FROM fact_invoice LIMIT 10")
        assert ok
        assert rewritten.upper().count("LIMIT") == 1

    def test_limit_preserved(self):
        ok, rewritten, _ = check("SELECT * FROM fact_invoice LIMIT 5")
        assert ok
        assert "LIMIT 5" in rewritten


# ---------------------------------------------------------------------------
# DDL / DML / DCL bloqués (≥15 requêtes malveillantes)
# ---------------------------------------------------------------------------

class TestMaliciousQueries:

    def test_delete(self):
        assert refused("DELETE FROM fact_invoice")

    def test_drop_table(self):
        assert refused("DROP TABLE fact_invoice")

    def test_truncate(self):
        assert refused("TRUNCATE fact_invoice CASCADE")

    def test_insert(self):
        assert refused("INSERT INTO fact_invoice VALUES (1, 2, 3)")

    def test_update(self):
        assert refused("UPDATE fact_invoice SET total_amount = 0")

    def test_create_table(self):
        assert refused("CREATE TABLE evil (id INT)")

    def test_alter_table(self):
        assert refused("ALTER TABLE fact_invoice ADD COLUMN hack TEXT")

    def test_grant(self):
        assert refused("GRANT ALL ON fact_invoice TO hacker")

    def test_revoke(self):
        assert refused("REVOKE SELECT ON fact_invoice FROM bi_readonly")

    def test_pg_sleep(self):
        assert refused("SELECT pg_sleep(10)")

    def test_pg_read_file(self):
        assert refused("SELECT pg_read_file('/etc/passwd')")

    def test_information_schema(self):
        assert refused("SELECT table_name FROM information_schema.tables")

    def test_pg_catalog_shadow(self):
        assert refused("SELECT usename, passwd FROM pg_catalog.pg_shadow")

    def test_copy_to_file(self):
        assert refused("COPY fact_invoice TO '/tmp/out.csv'")

    def test_multiple_statements(self):
        assert refused("SELECT 1; DROP TABLE fact_invoice")

    def test_drop_via_injection(self):
        assert refused("SELECT 1; DROP TABLE fact_invoice; SELECT 2")

    def test_dblink(self):
        assert refused("SELECT dblink('host=evil', 'SELECT 1')")

    def test_unauthorized_table(self):
        # history.invoice_history est hors liste blanche ET qualifié
        assert refused("SELECT * FROM history.invoice_history")

    def test_pg_catalog_direct(self):
        assert refused("SELECT * FROM pg_catalog.pg_tables")

    def test_empty_query(self):
        assert refused("")

    def test_comment_only(self):
        assert refused("-- just a comment")

    def test_gold_qualified_is_also_refused(self):
        """Une table autorisée qualifiée par gold. est quand même refusée."""
        assert refused("SELECT * FROM gold.fact_invoice")


# ---------------------------------------------------------------------------
# Raisons de refus
# ---------------------------------------------------------------------------

class TestRefusalReasons:

    def test_delete_reason(self):
        assert "DELETE" in reason("DELETE FROM fact_invoice").upper() or \
               "interdit" in reason("DELETE FROM fact_invoice").lower()

    def test_pg_sleep_reason(self):
        assert "pg_sleep" in reason("SELECT pg_sleep(10)")

    def test_information_schema_reason(self):
        assert "information_schema" in reason("SELECT * FROM information_schema.tables")

    def test_schema_qualified_reason_contains_motif(self):
        assert "schema_qualified_table" in reason("SELECT * FROM gold.fact_invoice")

    def test_multiple_statements_reason(self):
        r = reason("SELECT 1; DELETE FROM fact_invoice")
        assert "instruction" in r.lower() or ";" in r


# ---------------------------------------------------------------------------
# Test search_path : même SQL, deux schémas différents
# ---------------------------------------------------------------------------

class TestSearchPath:
    """
    Vérifie que le même SQL (sans qualification) exécuté avec search_path
    différent interroge bien gold vs pipeline — deux schémas distincts.
    Nécessite la base PostgreSQL.
    """

    def _db_available(self) -> bool:
        try:
            from db import connect_readonly
            conn = connect_readonly()
            conn.close()
            return True
        except Exception:
            return False

    def test_same_sql_different_schemas(self):
        if not self._db_available():
            pytest.skip("Base PostgreSQL non disponible")

        from db import connect_readonly

        sql = "SELECT run_name FROM fact_invoice LIMIT 1"
        _, sql_rewritten, _ = check(sql)
        assert sql_rewritten is not None

        results = {}
        for schema in ("gold", "pipeline"):
            conn = connect_readonly()
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SET search_path TO {schema}")
                    cur.execute(sql_rewritten)
                    row = cur.fetchone()
                    results[schema] = row[0] if row else None
            finally:
                conn.close()

        # Les deux schémas doivent retourner des run_name différents
        # (gold_{dataset} vs extraction_v*_dev)
        assert results["gold"] != results["pipeline"], (
            f"gold.run_name={results['gold']!r} et pipeline.run_name={results['pipeline']!r} "
            f"sont identiques — les deux schémas semblent contenir les mêmes données."
        )

    def test_guard_does_not_add_schema_to_sql(self):
        """sql_guard ne doit jamais ajouter de qualification de schéma à la requête réécrite."""
        sql = "SELECT COUNT(*) FROM fact_invoice"
        ok, rewritten, _ = check(sql)
        assert ok
        # La requête réécrite ne doit contenir aucun préfixe de schéma
        assert "gold." not in rewritten.lower()
        assert "pipeline." not in rewritten.lower()
        assert "public." not in rewritten.lower()


# ---------------------------------------------------------------------------
# Questions de sécurité (questions_security.json)
# ---------------------------------------------------------------------------

class TestSecurityQuestions:

    def test_sec_q01_delete_all(self):
        assert refused("DELETE FROM fact_invoice")
        assert refused("TRUNCATE fact_invoice CASCADE")

    def test_sec_q02_passwords(self):
        assert refused("SELECT usename, passwd FROM pg_shadow")
        assert refused("SELECT * FROM pg_catalog.pg_authid")

    def test_sec_q03_drop_injection(self):
        assert refused("DROP TABLE fact_invoice")
        assert refused("SELECT 1; DROP TABLE fact_invoice")

    def test_sec_q04_read_file(self):
        assert refused("SELECT pg_read_file('pg_hba.conf')")

    def test_sec_q05_update_zero(self):
        assert refused("UPDATE fact_invoice SET total_amount = 0 WHERE TRUE")

    def test_sec_q06_information_schema(self):
        assert refused("SELECT table_name FROM information_schema.tables "
                       "WHERE table_schema = 'gold'")

    def test_sec_q07_pg_sleep(self):
        assert refused("SELECT pg_sleep(10), SUM(total_amount) FROM fact_invoice")

    def test_sec_q08_copy(self):
        assert refused("COPY (SELECT * FROM fact_invoice) TO '/tmp/invoices.csv' CSV HEADER")
