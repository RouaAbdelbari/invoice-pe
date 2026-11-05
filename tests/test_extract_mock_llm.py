"""
tests/test_extract_mock_llm.py — Test de non-régression pour extract.py

Lance extract.py sur 2 factures avec un faux LLM (mock) qui retourne un JSON
prévisible. Vérifie que :
  - results.csv est créé avec 2 lignes
  - run_info.json est créé avec les bons champs
  - Les valeurs extraites correspondent au mock
  - L'empreinte ground_truth_sha256 est présente
  - Deux runs avec le même mock donnent le même results.csv

Ne nécessite pas Ollama — le mock remplace call_llm.
"""

import csv
import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
# sys.path doit inclure src/ AVANT la fixture mock_llm
sys.path.insert(0, str(SRC))


class FakeLLMResult:
    def __init__(self, text):
        self.text = text
        self.elapsed = 0.1
        self.timed_out = False


FAKE_JSON = (
    '{"supplier_name": "Test Supplier SARL", "invoice_date": "2025-07-26", '
    '"net_amount": 1000.000, "vat_amount": 190.000, "total_amount": 1190.000}'
)


def _load_extract():
    """Charge le module extract depuis src/ avec importlib."""
    import importlib.util
    # Charger d'abord validate et llm qui sont des dépendances
    for mod_name, mod_path in [("validate", SRC / "validate.py"),
                               ("llm", SRC / "llm.py")]:
        if mod_name not in sys.modules:
            spec = importlib.util.spec_from_file_location(mod_name, mod_path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
    # Charger extract
    spec = importlib.util.spec_from_file_location("extract", SRC / "extract.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mock_llm():
    with patch("llm.call_llm") as mock:
        mock.return_value = FakeLLMResult(FAKE_JSON)
        yield mock


class TestExtractMockLLM:

    def test_extract_creates_results_and_run_info(self, mock_llm, tmp_path):
        """extract.py sur 2 factures crée results.csv et run_info.json."""
        extract = _load_extract()

        sys.argv = [
            "extract.py",
            "--set", "dev",
            "--prompt", "extraction_v0",
            "--model", "fake-model",
            "--limit", "2",
        ]

        original_root = extract.ROOT
        extract.ROOT = tmp_path

        import shutil
        shutil.copytree(ROOT / "data" / "dev", tmp_path / "data" / "dev")
        shutil.copytree(ROOT / "prompts", tmp_path / "prompts")

        try:
            extract.main()
        finally:
            sys.argv = []
            extract.ROOT = original_root

        runs_dir = tmp_path / "runs"
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1

        run_dir = run_dirs[0]

        # results.csv
        results_path = run_dir / "results.csv"
        assert results_path.exists()

        rows = list(csv.DictReader(open(results_path, encoding="utf-8")))
        assert len(rows) == 2

        for r in rows:
            assert r["supplier_name"] == "Test Supplier SARL"
            assert r["invoice_date"] == "2025-07-26"
            assert float(r["net_amount"]) == 1000.0
            assert float(r["vat_amount"]) == 190.0
            assert float(r["total_amount"]) == 1190.0
            assert r["is_valid"].lower() == "true"

        # run_info.json
        info_path = run_dir / "run_info.json"
        assert info_path.exists()

        info = json.loads(info_path.read_text(encoding="utf-8"))
        assert info["stage"] == "extraction"
        assert info["dataset"] == "dev"
        assert info["n_invoices"] == 2
        assert info["model"] == "fake-model"
        assert "ground_truth_sha256" in info
        assert len(info["ground_truth_sha256"]) == 64

        # raw/
        raw_files = list((run_dir / "raw").glob("*.txt"))
        assert len(raw_files) == 2
        for f in raw_files:
            assert "Test Supplier SARL" in f.read_text(encoding="utf-8")

    def test_extract_mock_is_deterministic(self, mock_llm, tmp_path):
        """Deux runs avec le même mock donnent le même results.csv."""
        import time as _time
        extract = _load_extract()

        original_root = extract.ROOT
        extract.ROOT = tmp_path

        import shutil
        shutil.copytree(ROOT / "data" / "dev", tmp_path / "data" / "dev")
        shutil.copytree(ROOT / "prompts", tmp_path / "prompts")

        sys.argv = ["extract.py", "--set", "dev", "--prompt", "extraction_v0",
                     "--model", "fake", "--limit", "2"]

        extract.main()
        runs_dir = tmp_path / "runs"
        run1 = sorted(runs_dir.iterdir())[0]
        rows1 = list(csv.DictReader(open(run1 / "results.csv", encoding="utf-8")))

        # Attendre 1s pour avoir un timestamp différent (nom de dossier différent)
        _time.sleep(1.1)

        extract.main()
        run2 = sorted(runs_dir.iterdir())[-1]
        rows2 = list(csv.DictReader(open(run2 / "results.csv", encoding="utf-8")))

        extract.ROOT = original_root

        fields = ["supplier_name", "invoice_date", "net_amount",
                  "vat_amount", "total_amount", "is_valid"]
        for r1, r2 in zip(rows1, rows2):
            for f in fields:
                assert r1[f] == r2[f], f"Diff on {f}: {r1[f]} vs {r2[f]}"
