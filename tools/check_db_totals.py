"""
tools/check_db_totals.py — Point de contrôle B

Compare les totaux par fournisseur entre :
  1. gold.fact_invoice (base PostgreSQL, via bi_readonly)
     = factures is_inconsistent=False uniquement
  2. data/<set>/ground_truth.csv filtré sur is_inconsistent=False (calcul pandas)

Les deux doivent être identiques à 0.001 TND près.
Affiche aussi le nombre de factures dans gold.rejected_invoice.

Utilisation (depuis invoice-pe/) :
    python tools\\check_db_totals.py --set dev
    python tools\\check_db_totals.py --set test
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import connect_readonly

TOLERANCE = 0.001


def totals_from_db(dataset: str) -> pd.DataFrame:
    """Agrège par fournisseur depuis gold.fact_invoice via bi_readonly."""
    conn = connect_readonly()
    try:
        query = """
            SELECT
                ds.supplier_name,
                SUM(fi.net_amount)   AS net_amount,
                SUM(fi.vat_amount)   AS vat_amount,
                SUM(fi.total_amount) AS total_amount,
                COUNT(*)             AS n_invoices
            FROM gold.fact_invoice fi
            JOIN gold.dim_supplier ds USING (supplier_id)
            WHERE fi.run_name = %s
            GROUP BY ds.supplier_name
            ORDER BY ds.supplier_name
        """
        run_name = f"gold_{dataset}"
        with conn.cursor() as cur:
            cur.execute(query, (run_name,))
            cols = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()
    return df.set_index("supplier_name")


def totals_from_csv(dataset: str) -> pd.DataFrame:
    """Agrège par fournisseur depuis ground_truth.csv via pandas.
    Filtre sur is_inconsistent=False pour correspondre à gold.fact_invoice."""
    gt_path = ROOT / "data" / dataset / "ground_truth.csv"
    df = pd.read_csv(gt_path, dtype=str)
    df = df[df["is_inconsistent"].str.strip().str.lower() == "false"]
    for col in ("net_amount", "vat_amount", "total_amount"):
        df[col] = df[col].astype(float)
    agg = (
        df.groupby("supplier_name")[["net_amount", "vat_amount", "total_amount"]]
        .agg({"net_amount": "sum", "vat_amount": "sum", "total_amount": "sum"})
    )
    agg["n_invoices"] = df.groupby("supplier_name").size()
    return agg.sort_index()


def main():
    parser = argparse.ArgumentParser(description="Point de contrôle B : gold vs CSV")
    parser.add_argument("--set", choices=["dev", "test"], default="dev",
                        help="jeu de données à vérifier")
    args = parser.parse_args()

    print(f"Point de contrôle B — jeu : {args.set}")
    print("=" * 60)

    db_df = totals_from_db(args.set)
    csv_df = totals_from_csv(args.set)

    # Afficher aussi le nombre de rejetées dans gold
    conn_ro = connect_readonly()
    try:
        with conn_ro.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM gold.rejected_invoice WHERE run_name = %s",
                (f"gold_{args.set}",)
            )
            n_gold_rejected = cur.fetchone()[0]
    finally:
        conn_ro.close()
    print(f"  gold.rejected_invoice : {n_gold_rejected} factures incohérentes (exclues du check)")
    print()

    db_df = totals_from_db(args.set)
    csv_df = totals_from_csv(args.set)

    # Aligner les index
    all_suppliers = sorted(set(db_df.index) | set(csv_df.index))
    errors = []
    ok_count = 0

    for supplier in all_suppliers:
        if supplier not in db_df.index:
            errors.append(f"  MANQUANT en DB  : {supplier!r}")
            continue
        if supplier not in csv_df.index:
            errors.append(f"  MANQUANT en CSV : {supplier!r}")
            continue

        db_row = db_df.loc[supplier]
        csv_row = csv_df.loc[supplier]

        row_errors = []
        for col in ("net_amount", "vat_amount", "total_amount"):
            diff = abs(float(db_row[col]) - float(csv_row[col]))
            if diff > TOLERANCE:
                row_errors.append(
                    f"    {col}: DB={db_row[col]:.3f}  CSV={csv_row[col]:.3f}  "
                    f"ecart={diff:.4f}"
                )
        if int(db_row["n_invoices"]) != int(csv_row["n_invoices"]):
            row_errors.append(
                f"    n_invoices: DB={int(db_row['n_invoices'])}  "
                f"CSV={int(csv_row['n_invoices'])}"
            )

        if row_errors:
            errors.append(f"  ERREUR {supplier!r} :")
            errors.extend(row_errors)
        else:
            ok_count += 1
            print(f"  OK  {supplier:<40} "
                  f"total={float(db_row['total_amount']):>12.3f} TND  "
                  f"n={int(db_row['n_invoices'])}")

    print()
    if errors:
        print(f"ECHEC — {len(errors)} problème(s) détecté(s) :")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        print(f"SUCCES — {ok_count} fournisseurs vérifiés, "
              f"tous identiques à {TOLERANCE} TND près.")
        sys.exit(0)


if __name__ == "__main__":
    main()
