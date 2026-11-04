"""
tests/test_ground_truth_hash.py — Tests de la vérification d'empreinte ground_truth

Vérifie que :
  - evaluate_run refuse d'évaluer si l'empreinte stockée diffère de l'actuelle
  - evaluate_run avertit (sans planter) si l'empreinte est absente
  - compare_two_runs refuse si une empreinte est absente
  - compare_two_runs refuse si les deux empreintes diffèrent
  - compare_two_runs accepte deux runs avec la même empreinte valide
"""

import csv
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# Helpers pour créer des runs factices en mémoire
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_fake_run(tmp_path: Path, run_name: str, dataset: str,
                  gt_sha: str | None,
                  results: list[dict] | None = None) -> Path:
    """Crée un dossier de run minimal utilisable par evaluate_run."""
    run_dir = tmp_path / "runs" / run_name
    (run_dir / "raw").mkdir(parents=True)

    info = {
        "run": run_name,
        "stage": "extraction",
        "dataset": dataset,
        "n_invoices": 1,
        "prompt": "extraction_v0",
        "prompt_sha256_12": "aabbcc112233",
        "model": "qwen2.5:7b",
        "model_options": {},
        "started_at": "2026-11-04T10:00:00",
    }
    if gt_sha is not None:
        info["ground_truth_sha256"] = gt_sha

    (run_dir / "run_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    # results.csv minimal (une facture)
    if results is None:
        results = [{
            "invoice_id": "dev_001",
            "latency_s": "10.0",
            "json_strict": "False", "json_lenient": "True",
            "schema_ok": "True", "required_ok": "True", "types_ok": "True",
            "arithmetic_ok": "True", "vat_rate_ok": "True", "is_valid": "True",
            "supplier_name": "Haddad Pro SUARL",
            "invoice_date": "2025-07-26",
            "net_amount": "19262.090", "vat_amount": "1348.346",
            "total_amount": "20610.436",
            "errors": "",
        }]

    fields = list(results[0].keys())
    with open(run_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    return run_dir


def make_fake_gt(tmp_path: Path, dataset: str, content: str | None = None) -> Path:
    """Crée un ground_truth.csv factice, retourne son chemin."""
    gt_dir = tmp_path / "data" / dataset
    gt_dir.mkdir(parents=True, exist_ok=True)
    gt_path = gt_dir / "ground_truth.csv"
    if content is None:
        content = (
            "invoice_id,file,invoice_number,supplier_name,invoice_date,"
            "net_amount,vat_amount,total_amount,vat_rate,layout,language,"
            "date_format,number_format,is_inconsistent,inconsistency_type,anomaly_type\n"
            "dev_001,invoices/dev_001.txt,D-2025-0001,Haddad Pro SUARL,2025-07-26,"
            "19262.090,1348.346,20610.436,0.07,classic_fr,fr,dmy_slash,fr,False,,\n"
        )
    gt_path.write_text(content, encoding="utf-8")
    return gt_path


def patch_root(monkeypatch, tmp_path):
    """Redirige ROOT dans evaluate.py vers tmp_path."""
    import evaluate as ev
    monkeypatch.setattr(ev, "ROOT", tmp_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGroundTruthHash:

    def test_correct_hash_passes(self, monkeypatch, tmp_path):
        """Run avec empreinte correcte → évaluation sans erreur."""
        gt_path = make_fake_gt(tmp_path, "dev")
        sha = sha256_bytes(gt_path.read_bytes())
        run_dir = make_fake_run(tmp_path, "run_ok", "dev", gt_sha=sha)

        import evaluate as ev
        monkeypatch.setattr(ev, "ROOT", tmp_path)
        # Ne doit pas lever SystemExit
        summary, details = ev.evaluate_run(run_dir)
        assert summary["ground_truth_sha256"] == sha

    def test_wrong_hash_raises(self, monkeypatch, tmp_path):
        """Run avec empreinte incorrecte → SystemExit."""
        make_fake_gt(tmp_path, "dev")
        wrong_sha = "a" * 64   # empreinte délibérément fausse
        run_dir = make_fake_run(tmp_path, "run_bad_hash", "dev", gt_sha=wrong_sha)

        import evaluate as ev
        monkeypatch.setattr(ev, "ROOT", tmp_path)
        with pytest.raises(SystemExit) as exc:
            ev.evaluate_run(run_dir)
        assert "empreinte" in str(exc.value).lower() or "sha" in str(exc.value).lower()

    def test_missing_hash_warns_but_continues(self, monkeypatch, tmp_path, capsys):
        """Run sans empreinte → avertissement affiché, évaluation continue."""
        make_fake_gt(tmp_path, "dev")
        run_dir = make_fake_run(tmp_path, "run_no_hash", "dev", gt_sha=None)

        import evaluate as ev
        monkeypatch.setattr(ev, "ROOT", tmp_path)
        # Doit évaluer malgré l'absence d'empreinte
        summary, details = ev.evaluate_run(run_dir)
        captured = capsys.readouterr()
        assert "AVERT" in captured.out or "avert" in captured.out.lower()
        assert summary["ground_truth_sha256"] is None

    def test_compare_refuses_missing_hash(self, monkeypatch, tmp_path):
        """--compare refuse si une empreinte est absente."""
        gt_path = make_fake_gt(tmp_path, "dev")
        sha = sha256_bytes(gt_path.read_bytes())

        run_a = make_fake_run(tmp_path, "run_a", "dev", gt_sha=sha)
        run_b = make_fake_run(tmp_path, "run_b", "dev", gt_sha=None)

        import evaluate as ev
        monkeypatch.setattr(ev, "ROOT", tmp_path)

        with pytest.raises(SystemExit) as exc:
            ev.compare_two_runs(run_a, run_b)
        msg = str(exc.value).lower()
        assert "empreinte" in msg or "sha" in msg or "refus" in msg

    def test_compare_refuses_different_hashes(self, monkeypatch, tmp_path):
        """--compare refuse si les empreintes diffèrent entre les deux runs."""
        gt_path = make_fake_gt(tmp_path, "dev")
        sha_real = sha256_bytes(gt_path.read_bytes())
        sha_other = "b" * 64

        run_a = make_fake_run(tmp_path, "run_c", "dev", gt_sha=sha_real)
        run_b = make_fake_run(tmp_path, "run_d", "dev", gt_sha=sha_other)

        import evaluate as ev
        monkeypatch.setattr(ev, "ROOT", tmp_path)

        with pytest.raises(SystemExit) as exc:
            ev.compare_two_runs(run_a, run_b)
        msg = str(exc.value).lower()
        assert "empreinte" in msg or "diffèrent" in msg or "different" in msg

    def test_compare_accepts_same_hash(self, monkeypatch, tmp_path):
        """--compare accepte deux runs avec la même empreinte valide."""
        gt_path = make_fake_gt(tmp_path, "dev")
        sha = sha256_bytes(gt_path.read_bytes())

        run_a = make_fake_run(tmp_path, "run_e", "dev", gt_sha=sha)
        run_b = make_fake_run(tmp_path, "run_f", "dev", gt_sha=sha)

        import evaluate as ev
        monkeypatch.setattr(ev, "ROOT", tmp_path)

        # Ne doit pas lever SystemExit
        sum_a, sum_b, comparison = ev.compare_two_runs(run_a, run_b)
        assert sum_a["ground_truth_sha256"] == sha
        assert sum_b["ground_truth_sha256"] == sha

    def test_compare_refuses_missing_both(self, monkeypatch, tmp_path):
        """--compare refuse si les deux runs n'ont pas d'empreinte."""
        make_fake_gt(tmp_path, "dev")
        run_a = make_fake_run(tmp_path, "run_g", "dev", gt_sha=None)
        run_b = make_fake_run(tmp_path, "run_h", "dev", gt_sha=None)

        import evaluate as ev
        monkeypatch.setattr(ev, "ROOT", tmp_path)

        with pytest.raises(SystemExit):
            ev.compare_two_runs(run_a, run_b)
