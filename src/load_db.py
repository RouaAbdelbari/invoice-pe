"""
load_db.py — Chargement des données dans PostgreSQL (Phase B)

Modes :
  --target gold   --set dev|test
      Charge la vérité terrain depuis data/<set>/ground_truth.csv.
      - is_inconsistent=False → gold.fact_invoice  (vraies valeurs)
      - is_inconsistent=True  → gold.rejected_invoice (motif = inconsistency_type)

  --target pipeline --run <dossier>
      Charge les fiches extraites depuis runs/<dossier>/results.csv.
      - is_valid=True  → pipeline.fact_invoice
      - is_valid=False → pipeline.rejected_invoice
      Écrit une ligne dans pipeline.load_info (traçabilité).

  --target history
      Charge data/history/history.csv dans history.invoice_history.

Rechargement idempotent : TRUNCATE CASCADE + INSERT dans une transaction.
Les schémas et tables sont créés/vérifiés au démarrage (sql/schema.sql).

Règle noms de fournisseurs :
  Aucune normalisation, ni nettoyage, ni rapprochement approximatif.
  Un nom extrait différent du nom gold crée une nouvelle entrée dans
  pipeline.dim_supplier. C'est voulu : permet de mesurer la propagation
  des erreurs d'extraction (ex. "Haddad Pro SUARL, Nabeul") sur les
  requêtes BI (Phase C) et les agrégats (Phase D/E).

Utilisation (depuis invoice-pe/) :
    python src\\load_db.py --target gold --set dev
    python src\\load_db.py --target gold --set test
    python src\\load_db.py --target pipeline --run runs\\<dossier>
    python src\\load_db.py --target history
"""

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import connect_loader

MONTH_NAMES = {
    1: "Janvier", 2: "Février", 3: "Mars", 4: "Avril",
    5: "Mai", 6: "Juin", 7: "Juillet", 8: "Août",
    9: "Septembre", 10: "Octobre", 11: "Novembre", 12: "Décembre",
}

# Taux de TVA légaux (Tunisie). Utilisés pour normaliser le taux calculé en pipeline.
# Règle : le taux brut (vat/net) est arrondi au taux légal le plus proche.
# Cela garantit que dim_vat contient exactement les mêmes valeurs dans gold et pipeline,
# rendant les jointures et les comparaisons cohérentes.
# Si le taux brut s'écarte de plus de LEGAL_VAT_TOLERANCE d'un taux légal, on conserve
# la valeur brute arrondie à 4 décimales (cas d'extraction erronée, mesurable en évaluation).
LEGAL_VAT_RATES = [0.07, 0.13, 0.19]
LEGAL_VAT_TOLERANCE = 0.02   # ±2 points de tolérance pour le rapprochement


def snap_to_legal_vat(raw_rate: float) -> float:
    """Arrondit raw_rate au taux légal le plus proche si l'écart est dans LEGAL_VAT_TOLERANCE.

    Exemples :
      0.0700  → 0.07  (calcul exact)
      0.0701  → 0.07  (arrondi de montant, dans la tolérance)
      0.0699  → 0.07  (idem)
      0.2642  → 0.2642 (hors tolérance — cas d'extraction très erronée, conservé tel quel)

    Règle identique appliquée à gold (vat_rate lu depuis ground_truth.csv = déjà légal)
    et pipeline (vat_rate calculé depuis net_amount et vat_amount extraits).
    Documentée ici pour que les deux schémas soient comparables sur dim_vat.
    """
    best = min(LEGAL_VAT_RATES, key=lambda r: abs(raw_rate - r))
    if abs(raw_rate - best) <= LEGAL_VAT_TOLERANCE:
        return best
    return round(raw_rate, 4)


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def apply_schema(conn) -> None:
    """Crée/vérifie les tables (idempotent via CREATE IF NOT EXISTS)."""
    schema_sql = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def date_to_id(d: date) -> int:
    return int(d.strftime("%Y%m%d"))


def ensure_date(conn, schema: str, d: date) -> int:
    did = date_to_id(d)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {schema}.dim_date "
            "(date_id, full_date, year, quarter, month, month_name) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (did, d, d.year, (d.month - 1) // 3 + 1, d.month, MONTH_NAMES[d.month])
        )
    return did


def ensure_supplier(conn, schema: str, name: str) -> int:
    """Insère le fournisseur tel quel, sans aucune normalisation.

    Un nom différent (même d'un seul caractère) crée une nouvelle entrée.
    C'est voulu : permet de mesurer la propagation des erreurs d'extraction.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {schema}.dim_supplier (supplier_name) "
            "VALUES (%s) ON CONFLICT (supplier_name) DO NOTHING",
            (name,)
        )
        cur.execute(
            f"SELECT supplier_id FROM {schema}.dim_supplier WHERE supplier_name = %s",
            (name,)
        )
        return cur.fetchone()[0]


def ensure_vat(conn, schema: str, rate: float) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {schema}.dim_vat (vat_rate) "
            "VALUES (%s) ON CONFLICT (vat_rate) DO NOTHING",
            (rate,)
        )
        cur.execute(
            f"SELECT vat_id FROM {schema}.dim_vat WHERE vat_rate = %s",
            (rate,)
        )
        return cur.fetchone()[0]


def truncate_gold(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE gold.rejected_invoice CASCADE")
        cur.execute("TRUNCATE gold.fact_invoice CASCADE")
        cur.execute("TRUNCATE gold.dim_supplier CASCADE")
        cur.execute("TRUNCATE gold.dim_date CASCADE")
        cur.execute("TRUNCATE gold.dim_vat CASCADE")


def truncate_pipeline(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE pipeline.rejected_invoice CASCADE")
        cur.execute("TRUNCATE pipeline.fact_invoice CASCADE")
        cur.execute("TRUNCATE pipeline.dim_supplier CASCADE")
        cur.execute("TRUNCATE pipeline.dim_date CASCADE")
        cur.execute("TRUNCATE pipeline.dim_vat CASCADE")
        # load_info : on conserve l'historique des chargements, pas de TRUNCATE


# ---------------------------------------------------------------------------
# Chargement gold
# ---------------------------------------------------------------------------

def load_gold(conn, dataset: str) -> None:
    gt_path = ROOT / "data" / dataset / "ground_truth.csv"
    if not gt_path.exists():
        raise SystemExit(f"Fichier introuvable : {gt_path}")

    rows = list(csv.DictReader(open(gt_path, encoding="utf-8")))
    run_name = f"gold_{dataset}"

    consistent = [r for r in rows if r["is_inconsistent"].strip().lower() == "false"]
    inconsistent = [r for r in rows if r["is_inconsistent"].strip().lower() == "true"]

    print(f"Chargement gold ({dataset}) — {len(rows)} factures total")
    print(f"  {len(consistent)} cohérentes → gold.fact_invoice")
    print(f"  {len(inconsistent)} incohérentes → gold.rejected_invoice")

    with conn:
        truncate_gold(conn)

        # Factures cohérentes → fact_invoice
        for row in consistent:
            inv_date = date.fromisoformat(row["invoice_date"])
            supplier_id = ensure_supplier(conn, "gold", row["supplier_name"])
            date_id_val = ensure_date(conn, "gold", inv_date)
            # Taux lu depuis ground_truth.csv (déjà légal), snap_to_legal_vat confirme
            vat_id = ensure_vat(conn, "gold", snap_to_legal_vat(float(row["vat_rate"])))

            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO gold.fact_invoice "
                    "(invoice_id, invoice_number, supplier_id, date_id, vat_id, "
                    "net_amount, vat_amount, total_amount, is_valid, source_file, run_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        row["invoice_id"],
                        row["invoice_number"],
                        supplier_id,
                        date_id_val,
                        vat_id,
                        float(row["net_amount"]),
                        float(row["vat_amount"]),
                        float(row["total_amount"]),
                        True,
                        row["file"],
                        run_name,
                    )
                )

        # Factures incohérentes → rejected_invoice
        for row in inconsistent:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO gold.rejected_invoice "
                    "(invoice_id, run_name, errors, raw_json) "
                    "VALUES (%s, %s, %s, %s)",
                    (
                        row["invoice_id"],
                        run_name,
                        row["inconsistency_type"],   # wrong_total | wrong_vat
                        None,
                    )
                )

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM gold.fact_invoice")
        n_f = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM gold.rejected_invoice")
        n_r = cur.fetchone()[0]
    print(f"  gold.fact_invoice    : {n_f} lignes")
    print(f"  gold.rejected_invoice: {n_r} lignes")


# ---------------------------------------------------------------------------
# Chargement pipeline
# ---------------------------------------------------------------------------

def load_pipeline(conn, run_path: Path) -> None:
    results_path = run_path / "results.csv"
    info_path = run_path / "run_info.json"
    if not results_path.exists():
        raise SystemExit(f"results.csv introuvable dans {run_path}")
    if not info_path.exists():
        raise SystemExit(f"run_info.json introuvable dans {run_path}")

    info = json.loads(info_path.read_text(encoding="utf-8"))
    run_name = info["run"]
    dataset = info["dataset"]
    rows = list(csv.DictReader(open(results_path, encoding="utf-8")))

    # Vérification d'empreinte
    gt_path = ROOT / "data" / dataset / "ground_truth.csv"
    current_sha = sha256_file(gt_path)
    stored_sha = info.get("ground_truth_sha256")
    if stored_sha and stored_sha != current_sha:
        raise SystemExit(
            f"ERREUR : empreinte ground_truth_sha256 du run '{run_name}' "
            f"ne correspond plus au fichier actuel.\n"
            f"  stockée  : {stored_sha}\n"
            f"  actuelle : {current_sha}"
        )
    if not stored_sha:
        print(f"  AVERT : run sans empreinte (ancien run). Chargement continue.")

    valid_rows = [r for r in rows if r["is_valid"].strip().lower() == "true"]
    rejected_rows = [r for r in rows if r["is_valid"].strip().lower() != "true"]

    print(f"Chargement pipeline — run : {run_name}")
    print(f"  {len(valid_rows)} valides → pipeline.fact_invoice")
    print(f"  {len(rejected_rows)} rejetées → pipeline.rejected_invoice")

    with conn:
        truncate_pipeline(conn)

        # Fiches rejetées
        for row in rejected_rows:
            raw_path = run_path / "raw" / f"{row['invoice_id']}.txt"
            raw_json = raw_path.read_text(encoding="utf-8") if raw_path.exists() else None
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pipeline.rejected_invoice "
                    "(invoice_id, run_name, errors, raw_json) "
                    "VALUES (%s, %s, %s, %s)",
                    (row["invoice_id"], run_name, row["errors"], raw_json)
                )

        # Fiches valides
        n_skipped = 0
        for row in valid_rows:
            try:
                inv_date = date.fromisoformat(row["invoice_date"])
            except (ValueError, TypeError):
                print(f"  AVERT {row['invoice_id']} : date invalide '{row['invoice_date']}' "
                      f"— ignorée")
                n_skipped += 1
                continue

            # Aucune normalisation du nom — voir commentaire dans schema.sql
            supplier_id = ensure_supplier(conn, "pipeline", row["supplier_name"])
            date_id_val = ensure_date(conn, "pipeline", inv_date)

            try:
                net = float(row["net_amount"])
                vat = float(row["vat_amount"])
                raw_rate = vat / net if net > 0 else 0.0
            except (ValueError, ZeroDivisionError):
                raw_rate = 0.0
            # Arrondi au taux légal le plus proche — même règle que gold
            vat_id = ensure_vat(conn, "pipeline", snap_to_legal_vat(raw_rate))

            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pipeline.fact_invoice "
                    "(invoice_id, invoice_number, supplier_id, date_id, vat_id, "
                    "net_amount, vat_amount, total_amount, is_valid, source_file, run_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        row["invoice_id"],
                        row["invoice_id"],
                        supplier_id,
                        date_id_val,
                        vat_id,
                        float(row["net_amount"]),
                        float(row["vat_amount"]),
                        float(row["total_amount"]),
                        True,
                        None,
                        run_name,
                    )
                )

        n_loaded = len(valid_rows) - n_skipped

        # Traçabilité : load_info
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline.load_info "
                "(run_name, dataset, ground_truth_sha256, n_loaded, n_rejected) "
                "VALUES (%s, %s, %s, %s, %s)",
                (run_name, dataset, stored_sha or current_sha,
                 n_loaded, len(rejected_rows))
            )

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM pipeline.fact_invoice")
        n_f = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pipeline.rejected_invoice")
        n_r = cur.fetchone()[0]
    print(f"  pipeline.fact_invoice    : {n_f} lignes")
    print(f"  pipeline.rejected_invoice: {n_r} lignes")
    print(f"  pipeline.load_info       : run '{run_name}' enregistré")


# ---------------------------------------------------------------------------
# Chargement history
# ---------------------------------------------------------------------------

def load_history(conn) -> None:
    hist_path = ROOT / "data" / "history" / "history.csv"
    if not hist_path.exists():
        raise SystemExit(f"Fichier introuvable : {hist_path}")

    rows = list(csv.DictReader(open(hist_path, encoding="utf-8")))
    print(f"Chargement history — {len(rows)} factures")

    with conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE history.invoice_history CASCADE")
        psycopg2.extras.execute_values(
            conn.cursor(),
            "INSERT INTO history.invoice_history "
            "(invoice_number, supplier_name, invoice_date, net_amount, vat_amount, "
            "total_amount, vat_rate, n_lines, anomaly_type) VALUES %s",
            [
                (
                    r["invoice_number"], r["supplier_name"],
                    date.fromisoformat(r["invoice_date"]),
                    float(r["net_amount"]), float(r["vat_amount"]),
                    float(r["total_amount"]), float(r["vat_rate"]),
                    int(r["n_lines"]), r["anomaly_type"] or "",
                )
                for r in rows
            ],
            page_size=100,
        )

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM history.invoice_history")
        n = cur.fetchone()[0]
    print(f"  history.invoice_history : {n} lignes")


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Chargement des données dans PostgreSQL")
    parser.add_argument("--target", choices=["gold", "pipeline", "history"], required=True)
    parser.add_argument("--set", default=None, dest="dataset",
                        help="jeu de données (requis pour --target gold ; dev, test, ou jeu extra)")
    parser.add_argument("--run", default=None,
                        help="dossier du run (requis pour --target pipeline)")
    args = parser.parse_args()

    if args.target == "gold" and not args.dataset:
        raise SystemExit("--target gold requiert --set dev|test")
    if args.target == "pipeline" and not args.run:
        raise SystemExit("--target pipeline requiert --run <dossier>")

    conn = connect_loader()
    try:
        print("Connexion PostgreSQL OK")
        apply_schema(conn)
        print("Schema verifie/cree")

        if args.target == "gold":
            load_gold(conn, args.dataset)
        elif args.target == "pipeline":
            run_path = Path(args.run)
            if not run_path.is_absolute():
                run_path = ROOT / run_path
            if not run_path.exists():
                raise SystemExit(f"Dossier introuvable : {run_path}")
            load_pipeline(conn, run_path)
        elif args.target == "history":
            load_history(conn)

        print("Chargement termine avec succes.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
