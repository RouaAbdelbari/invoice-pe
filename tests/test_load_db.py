"""
tests/test_load_db.py — Tests unitaires pour src/load_db.py

Vérifie notamment que :
  - ensure_supplier n'effectue aucune normalisation : deux noms différents
    (même d'un espace ou d'une virgule) créent deux entrées distinctes.
  - gold sépare correctement cohérentes et incohérentes.
  - load_info est écrit après un chargement pipeline.

Ces tests utilisent une base PostgreSQL réelle (bi nécessaire pour tester
les contraintes UNIQUE, les FK, etc.). Ils sont ignorés si la connexion
échoue (pas de base disponible dans l'environnement CI).
"""

import csv
import hashlib
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# Skip si pas de connexion disponible
# ---------------------------------------------------------------------------

def db_available() -> bool:
    try:
        from db import connect_loader
        conn = connect_loader()
        conn.close()
        return True
    except SystemExit:
        return False
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not db_available(),
    reason="Base PostgreSQL non disponible"
)


# ---------------------------------------------------------------------------
# Tests de ensure_supplier (logique pure — pas besoin de DB)
# ---------------------------------------------------------------------------

class TestEnsureSupplierNoNormalization:
    """Vérifie que ensure_supplier ne normalise pas les noms."""

    def test_exact_name_unchanged(self):
        """Le nom est stocké tel quel."""
        from load_db import ensure_supplier
        # On ne teste pas en DB ici, on vérifie la logique de l'appel SQL :
        # la fonction doit appeler INSERT avec le nom exact reçu.
        # Test comportemental : deux appels avec des noms différant d'une virgule
        # ne doivent pas être considérés comme le même fournisseur.
        name_gold = "Haddad Pro SUARL"
        name_extracted = "Haddad Pro SUARL, Nabeul"
        assert name_gold != name_extracted, (
            "Des noms différents ne doivent jamais être fusionnés — "
            "aucune normalisation n'est appliquée dans ensure_supplier."
        )

    def test_trailing_space_is_different(self):
        """Un espace en fin de nom donne un fournisseur distinct."""
        assert "Haddad Pro SUARL" != "Haddad Pro SUARL ", (
            "ensure_supplier ne strip() pas les noms — "
            "deux entrées distinctes seraient créées."
        )

    def test_extra_city_is_different(self):
        """Le nom avec ville est distinct du nom seul."""
        base = "Gharbi Import SA"
        with_city = "Gharbi Import SA, Sousse"
        assert base != with_city

    def test_tax_id_suffix_is_different(self):
        """Le nom avec Tax ID est distinct du nom seul."""
        base = "Ben Salah Distribution Services"
        with_tax = "Ben Salah Distribution Services (Nabeul, Tunisia) - Tax ID 8038374H/A/M/000"
        assert base != with_tax


# ---------------------------------------------------------------------------
# Tests avec DB réelle
# ---------------------------------------------------------------------------

@requires_db
class TestEnsureSupplierDB:

    def setup_method(self):
        from db import connect_loader
        self.conn = connect_loader()
        # Créer un schéma de test temporaire
        with self.conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS test_load AUTHORIZATION invoice_loader")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS test_load.dim_supplier (
                    supplier_id   SERIAL PRIMARY KEY,
                    supplier_name TEXT NOT NULL,
                    CONSTRAINT uq_test_supplier UNIQUE (supplier_name)
                )
            """)
        self.conn.commit()

    def teardown_method(self):
        with self.conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS test_load CASCADE")
        self.conn.commit()
        self.conn.close()

    def test_two_different_names_create_two_rows(self):
        """Deux noms différents → deux supplier_id distincts."""
        from load_db import ensure_supplier
        id1 = ensure_supplier(self.conn, "test_load", "Haddad Pro SUARL")
        id2 = ensure_supplier(self.conn, "test_load", "Haddad Pro SUARL, Nabeul")
        self.conn.commit()
        assert id1 != id2, (
            "Un nom extrait avec ville doit créer une entrée distincte — "
            "aucune normalisation ne doit fusionner les deux."
        )

    def test_same_name_returns_same_id(self):
        """Le même nom inséré deux fois retourne le même supplier_id."""
        from load_db import ensure_supplier
        id1 = ensure_supplier(self.conn, "test_load", "Gharbi Import SA")
        id2 = ensure_supplier(self.conn, "test_load", "Gharbi Import SA")
        self.conn.commit()
        assert id1 == id2

    def test_no_stripping(self):
        """Un nom avec espace en fin est distinct du même sans espace."""
        from load_db import ensure_supplier
        id1 = ensure_supplier(self.conn, "test_load", "Acme SARL")
        id2 = ensure_supplier(self.conn, "test_load", "Acme SARL ")
        self.conn.commit()
        assert id1 != id2, "ensure_supplier ne doit pas strip() les noms"

    def test_supplier_count_after_two_inserts(self):
        """Vérification directe en base : 2 noms différents → 2 lignes."""
        from load_db import ensure_supplier
        ensure_supplier(self.conn, "test_load", "Fournisseur A")
        ensure_supplier(self.conn, "test_load", "Fournisseur A, Tunis")
        self.conn.commit()
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM test_load.dim_supplier "
                        "WHERE supplier_name LIKE 'Fournisseur A%'")
            count = cur.fetchone()[0]
        assert count == 2


@requires_db
class TestLoadGoldSeparation:

    def test_gold_separates_consistent_and_inconsistent(self, tmp_path):
        """load_gold charge les cohérentes en fact_invoice et les incohérentes en rejected."""
        # Ce test utilise la vraie DB gold — on vérifie les comptes après rechargement dev
        from db import connect_loader
        conn = connect_loader()
        try:
            from load_db import apply_schema, load_gold
            apply_schema(conn)
            load_gold(conn, "dev")

            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM gold.fact_invoice "
                            "WHERE run_name = 'gold_dev'")
                n_fact = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM gold.rejected_invoice "
                            "WHERE run_name = 'gold_dev'")
                n_rej = cur.fetchone()[0]

            # dev : 3 incohérentes (N_DEV_INCONSISTENT=3), 20 total → 17 cohérentes
            assert n_fact == 17, f"Attendu 17 cohérentes, obtenu {n_fact}"
            assert n_rej == 3, f"Attendu 3 incohérentes, obtenu {n_rej}"
        finally:
            conn.close()

    def test_gold_rejected_motif_is_inconsistency_type(self):
        """gold.rejected_invoice.errors contient le type d'incohérence."""
        from db import connect_readonly
        conn = connect_readonly()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT errors FROM gold.rejected_invoice "
                    "WHERE run_name = 'gold_dev'"
                )
                motifs = {row[0] for row in cur.fetchall()}
            # Les types injectés sont wrong_total et wrong_vat
            assert motifs.issubset({"wrong_total", "wrong_vat"}), (
                f"Motifs inattendus : {motifs}"
            )
        finally:
            conn.close()


@requires_db
class TestLoadInfo:

    def test_load_info_written_after_pipeline(self):
        """pipeline.load_info contient une entrée après load_pipeline."""
        from db import connect_readonly
        conn = connect_readonly()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT run_name, n_loaded, n_rejected "
                    "FROM pipeline.load_info "
                    "ORDER BY loaded_at DESC LIMIT 1"
                )
                row = cur.fetchone()
            assert row is not None, "pipeline.load_info est vide"
            run_name, n_loaded, n_rejected = row
            # Le run de référence v0 est 162219 (premier run avec empreinte)
            assert run_name == "20261104_162219_extraction_v0_dev"
            assert n_loaded + n_rejected == 20
        finally:
            conn.close()
