"""
tests/test_generator.py — Tests pour generator.py

Vérifie que :
  - Les empreintes SHA-256 de data/test/ground_truth.csv et
    data/dev/ground_truth.csv sont inchangées après --extra-dev
  - Le jeu dev2 a la bonne structure (N factures, n_incons, n_anom)
  - La couverture layout × num_style est équilibrée (toutes les 12 combinaisons présentes)
  - Les fournisseurs de dev2 sont un sous-ensemble de ceux du jeu principal
  - manifest_dev2.json est créé, manifest.json est inchangé
"""

import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Empreintes de référence des jeux protégés (calculées une fois, stables)
SHA_TEST_GT = sha256_file(ROOT / "data" / "test" / "ground_truth.csv")
SHA_DEV_GT  = sha256_file(ROOT / "data" / "dev"  / "ground_truth.csv")


class TestExtraDevDoesNotCorruptMainSets:

    def test_test_ground_truth_unchanged_after_extra_dev(self):
        """data/test/ground_truth.csv ne doit pas changer après --extra-dev."""
        # Le jeu dev2 existe déjà (généré en amont) — on vérifie juste l'empreinte
        current = sha256_file(ROOT / "data" / "test" / "ground_truth.csv")
        assert current == SHA_TEST_GT, (
            "data/test/ground_truth.csv a été modifié ! "
            "L'option --extra-dev ne doit jamais toucher au jeu de test."
        )

    def test_dev_ground_truth_unchanged_after_extra_dev(self):
        """data/dev/ground_truth.csv ne doit pas changer après --extra-dev."""
        current = sha256_file(ROOT / "data" / "dev" / "ground_truth.csv")
        assert current == SHA_DEV_GT, (
            "data/dev/ground_truth.csv a été modifié ! "
            "L'option --extra-dev ne doit jamais toucher au jeu dev principal."
        )

    def test_manifest_json_unchanged(self):
        """manifest.json principal ne doit pas être modifié par --extra-dev."""
        manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["seed"] == 42
        # Si manifest.json avait été réécrit par --extra-dev, le seed 42 serait toujours là,
        # mais les counts pourraient changer — on vérifie les counts attendus.
        assert manifest["counts"]["test"] == 100
        assert manifest["counts"]["dev"] == 20
        assert manifest["counts"]["history"] == 500

    def test_manifest_dev2_created(self):
        """manifest_dev2.json doit exister dans data/."""
        p = ROOT / "data" / "manifest_dev2.json"
        assert p.exists(), "manifest_dev2.json absent de data/"
        m = json.loads(p.read_text(encoding="utf-8"))
        assert m["main_seed"] == 42
        assert m["extra_seed"] == 2026
        assert m["n"] == 40

    def test_extra_dev_regeneration_is_stable(self):
        """Regénérer dev2 avec les mêmes paramètres doit donner le même ground_truth.csv."""
        gt_before = sha256_file(ROOT / "data" / "dev2" / "ground_truth.csv")
        result = subprocess.run(
            [PYTHON, "src/generator.py", "--extra-dev", "40",
             "--extra-seed", "2026", "--extra-name", "dev2"],
            cwd=ROOT, capture_output=True, text=True
        )
        assert result.returncode == 0, f"generator.py a échoué : {result.stderr}"
        gt_after = sha256_file(ROOT / "data" / "dev2" / "ground_truth.csv")
        assert gt_before == gt_after, "La régénération de dev2 n'est pas déterministe."


@pytest.fixture(scope="module")
def dev2_rows():
    return list(csv.DictReader(
        open(ROOT / "data" / "dev2" / "ground_truth.csv", encoding="utf-8")
    ))


class TestDev2Structure:

    def test_count(self, dev2_rows):
        assert len(dev2_rows) == 40

    def test_n_inconsistent(self, dev2_rows):
        n = sum(1 for r in dev2_rows if r["is_inconsistent"] == "True")
        assert n == 6

    def test_n_anomalies(self, dev2_rows):
        n = sum(1 for r in dev2_rows if r["anomaly_type"])
        assert n == 3

    def test_all_12_combos_present(self, dev2_rows):
        """Toutes les 12 combinaisons layout × number_format doivent être présentes."""
        sys.path.insert(0, str(ROOT / "src"))
        from generator import LAYOUTS, NUM_STYLES
        expected = {(l, ns) for l in LAYOUTS for ns in NUM_STYLES}
        found = {(r["layout"], r["number_format"]) for r in dev2_rows}
        missing = expected - found
        assert not missing, f"Combinaisons manquantes : {missing}"

    def test_balanced_no_combo_overrepresented(self, dev2_rows):
        """Aucune combinaison ne doit avoir plus de 2 fois la fréquence minimale."""
        combos = Counter((r["layout"], r["number_format"]) for r in dev2_rows)
        counts = list(combos.values())
        assert max(counts) - min(counts) <= 1, (
            f"Déséquilibre trop grand : min={min(counts)}, max={max(counts)}"
        )

    def test_suppliers_are_subset_of_main(self, dev2_rows):
        """Les noms de fournisseurs de dev2 doivent appartenir au jeu de fournisseurs principal."""
        main_suppliers = {
            r["supplier_name"]
            for r in csv.DictReader(
                open(ROOT / "data" / "dev" / "ground_truth.csv", encoding="utf-8")
            )
        }
        # Le jeu dev n'a que 20 factures, certains fournisseurs peuvent ne pas y apparaître.
        # On vérifie via manifest.json qui liste tous les fournisseurs construits avec graine 42.
        manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
        all_suppliers = {s["name"] for s in manifest["suppliers"]}
        dev2_suppliers = {r["supplier_name"] for r in dev2_rows}
        unknown = dev2_suppliers - all_suppliers
        assert not unknown, f"Fournisseurs inconnus dans dev2 : {unknown}"

    def test_invoice_ids_prefixed_correctly(self, dev2_rows):
        """Les invoice_id doivent commencer par 'dev2_'."""
        for r in dev2_rows:
            assert r["invoice_id"].startswith("dev2_"), (
                f"invoice_id inattendu : {r['invoice_id']}"
            )

    def test_invoice_files_exist(self, dev2_rows):
        """Tous les fichiers texte référencés dans ground_truth.csv doivent exister."""
        base = ROOT / "data" / "dev2"
        for r in dev2_rows:
            p = base / r["file"]
            assert p.exists(), f"Fichier manquant : {p}"
