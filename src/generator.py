"""
generator.py — Générateur de factures synthétiques
Projet : Prompt Engineering pour l'extraction fiable d'informations de factures
         et l'interrogation de bases de données en langage naturel

Ce script produit trois jeux de données, tous reproductibles grâce à une graine fixe :

  data/test/      40 factures texte + vérité terrain  -> évaluation des prompts (ne JAMAIS s'en servir pour concevoir les prompts)
  data/dev/       10 factures texte + vérité terrain  -> exemples few-shot et amélioration des prompts
  data/history/   500 factures structurées (CSV)      -> entraînement du modèle d'anomalies (Isolation Forest)

Ce que contient chaque facture texte :
  - plusieurs mises en page (4 gabarits), en français ou en anglais
  - plusieurs formats de dates (12/03/2025, 2025-03-12, 12 mars 2025, March 12, 2025)
  - plusieurs formats de nombres (1 234,567 / 1,234.567 / 1234.567)
  - des informations « pièges » (nom du client, adresse, conditions de paiement) pour tester l'extraction

Deux types de cas particuliers sont injectés volontairement :
  - INCOHÉRENCES arithmétiques (HT + TVA ≠ TTC, ou TVA ≠ HT × taux) -> à détecter par la validation Python
  - ANOMALIES métier (montant atypique pour un fournisseur, doublon, taux de TVA inhabituel) -> à détecter par le modèle IA

Utilisation :
    pip install faker
    python generator.py              # graine 42, sortie dans ./data
    python generator.py --seed 7 --out mes_donnees
"""

import argparse
import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

# ---------------------------------------------------------------------------
# 1. PARAMÈTRES GÉNÉRAUX (modifiables)
# ---------------------------------------------------------------------------

N_TEST = 100         # factures du jeu de test
N_DEV = 20           # factures du jeu de développement
N_HISTORY = 500      # factures de l'historique (pour le modèle d'anomalies)

N_TEST_INCONSISTENT = 15  # factures de test avec une erreur arithmétique injectée
N_TEST_ANOMALIES = 12     # factures de test avec une anomalie métier injectée
N_DEV_INCONSISTENT = 3
N_DEV_ANOMALIES = 2
HISTORY_ANOMALY_RATE = 0.05   # 5 % d'anomalies dans l'historique

# Périodes : l'historique précède les factures de test/dev (comme dans la réalité)
HISTORY_START, HISTORY_END = date(2024, 1, 1), date(2025, 6, 30)
RECENT_START, RECENT_END = date(2025, 7, 1), date(2025, 12, 31)

VAT_RATES = [0.19, 0.13, 0.07]   # taux de TVA tunisiens
CURRENCY = "TND"                  # dinar tunisien : 3 décimales (millimes)

MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]

# Noms de famille utilisés pour nommer les fournisseurs (entreprises fictives)
TN_NAMES = ["Ben Salah", "Gharbi", "Jaziri", "Hammami", "Mansouri", "Karoui", "Bouzid", "Chaabane",
            "Mejri", "Sassi", "Ayari", "Khelifi", "Dridi", "Ferchichi", "Haddad", "Zouari"]

# Articles possibles, regroupés par secteur du fournisseur
CATALOG = {
    "informatique": ["Ordinateur portable", "Écran 24 pouces", "Licence logicielle annuelle",
                     "Maintenance serveur", "Clavier et souris", "Disque SSD 1 To"],
    "fournitures":  ["Ramette papier A4", "Cartouche d'encre", "Classeurs (lot de 10)",
                     "Stylos (boîte de 50)", "Agrafeuse", "Enveloppes (lot de 100)"],
    "logistique":   ["Transport de marchandises", "Location de camion", "Palettes",
                     "Stockage mensuel", "Emballage carton", "Livraison express"],
    "services":     ["Prestation de conseil", "Formation (journée)", "Audit qualité",
                     "Nettoyage des locaux", "Assistance technique", "Traduction"],
    "energie":      ["Gasoil (litres)", "Maintenance groupe électrogène", "Pièces détachées",
                     "Huile moteur", "Filtres", "Intervention technicien"],
}


# ---------------------------------------------------------------------------
# 2. FOURNISSEURS RÉCURRENTS
#    Chaque fournisseur a un secteur, un taux de TVA habituel et une fourchette
#    de prix habituelle : c'est ce qui permet au modèle IA d'apprendre le « normal ».
# ---------------------------------------------------------------------------

def build_suppliers(rng: random.Random, fake: Faker, n: int = 12) -> list[dict]:
    suffixes = ["SARL", "SA", "SUARL", "& Fils", "Group", "Services"]
    sectors = list(CATALOG.keys())
    family_names = rng.sample(TN_NAMES, n)   # noms d'entreprises à consonance tunisienne, tous distincts
    suppliers = []
    for i in range(n):
        sector = sectors[i % len(sectors)]
        name = f"{family_names[i]} {rng.choice(['Tech', 'Distribution', 'Trading', 'Pro', 'Solutions', 'Import'])} {rng.choice(suffixes)}"
        base = rng.choice([80, 150, 300, 600, 1200, 2500])   # ordre de grandeur des prix unitaires
        suppliers.append({
            "name": name,
            "sector": sector,
            "usual_vat": rng.choice(VAT_RATES),
            "unit_price_range": (base * 0.5, base * 1.5),
            "city": rng.choice(["Tunis", "Sousse", "Sfax", "Monastir", "Nabeul", "Bizerte"]),
            "tax_id": f"{rng.randint(1000000, 9999999)}{rng.choice('ABCDEFGHJKLMNPQRSTVWXYZ')}/A/M/000",
        })
    return suppliers


def random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


# ---------------------------------------------------------------------------
# 3. CRÉATION D'UNE FACTURE (valeurs « vraies »)
# ---------------------------------------------------------------------------

def make_invoice(rng: random.Random, supplier: dict, inv_date: date, inv_number: str) -> dict:
    n_lines = rng.randint(1, 5)
    lines = []
    for _ in range(n_lines):
        label = rng.choice(CATALOG[supplier["sector"]])
        qty = rng.randint(1, 10)
        unit = round(rng.uniform(*supplier["unit_price_range"]), 3)
        lines.append({"label": label, "qty": qty, "unit": unit, "amount": round(qty * unit, 3)})

    net = round(sum(l["amount"] for l in lines), 3)
    rate = supplier["usual_vat"]
    vat = round(net * rate, 3)
    total = round(net + vat, 3)
    return {
        "invoice_number": inv_number,
        "supplier_name": supplier["name"],
        "supplier": supplier,
        "invoice_date": inv_date,
        "lines": lines,
        "vat_rate": rate,
        "net_amount": net,
        "vat_amount": vat,
        "total_amount": total,
        "is_inconsistent": False,
        "inconsistency_type": "",
        "anomaly_type": "",
    }


# ---------------------------------------------------------------------------
# 4. INJECTION DES CAS PARTICULIERS
# ---------------------------------------------------------------------------

def inject_inconsistency(rng: random.Random, inv: dict) -> None:
    """Erreur arithmétique imprimée sur la facture. La vérité terrain garde
    les valeurs IMPRIMÉES (l'extraction doit lire ce qui est écrit) et marque
    la facture comme incohérente (la validation Python doit la détecter)."""
    kind = rng.choice(["wrong_total", "wrong_vat"])
    if kind == "wrong_total":
        inv["total_amount"] = round(inv["total_amount"] + rng.choice([-1, 1]) * rng.uniform(5, 200), 3)
    else:
        inv["vat_amount"] = round(inv["vat_amount"] * rng.uniform(1.15, 1.5), 3)
        inv["total_amount"] = round(inv["net_amount"] + inv["vat_amount"], 3)  # TTC cohérent avec la mauvaise TVA
    inv["is_inconsistent"] = True
    inv["inconsistency_type"] = kind


def inject_anomaly(rng: random.Random, inv: dict, previous: list[dict]) -> None:
    """Anomalie métier : la facture est arithmétiquement correcte, mais suspecte."""
    kind = rng.choice(["amount_outlier", "unusual_vat", "duplicate"])
    if kind == "duplicate":
        same = [p for p in previous if p["supplier_name"] == inv["supplier_name"] and not p["anomaly_type"]]
        if not same:
            kind = "amount_outlier"   # pas de facture à dupliquer : on se rabat sur un montant atypique
        else:
            src = rng.choice(same)
            inv["lines"] = [dict(l) for l in src["lines"]]
            inv["vat_rate"] = src["vat_rate"]
            inv["invoice_date"] = src["invoice_date"] + timedelta(days=rng.randint(0, 5))
    if kind == "amount_outlier":
        factor = rng.uniform(6, 12)
        for l in inv["lines"]:
            l["unit"] = round(l["unit"] * factor, 3)
            l["amount"] = round(l["qty"] * l["unit"], 3)
    if kind == "unusual_vat":
        inv["vat_rate"] = rng.choice([r for r in VAT_RATES if r != inv["supplier"]["usual_vat"]])
    # On recalcule les totaux pour que la facture reste arithmétiquement correcte
    inv["net_amount"] = round(sum(l["amount"] for l in inv["lines"]), 3)
    inv["vat_amount"] = round(inv["net_amount"] * inv["vat_rate"], 3)
    inv["total_amount"] = round(inv["net_amount"] + inv["vat_amount"], 3)
    inv["anomaly_type"] = kind


# ---------------------------------------------------------------------------
# 5. MISE EN FORME TEXTE (dates, nombres, gabarits)
# ---------------------------------------------------------------------------

def fmt_date(d: date, style: str) -> str:
    if style == "dmy_slash":
        return d.strftime("%d/%m/%Y")
    if style == "iso":
        return d.isoformat()
    if style == "long_fr":
        return f"{d.day} {MONTHS_FR[d.month - 1]} {d.year}"
    return f"{MONTHS_EN[d.month - 1]} {d.day}, {d.year}"          # long_en


def fmt_num(x: float, style: str) -> str:
    s = f"{x:,.3f}"                                              # 1,234.567
    if style == "fr":
        return s.replace(",", " ").replace(".", ",")             # 1 234,567
    if style == "plain":
        return s.replace(",", "")                                # 1234.567
    return s                                                     # en


def render(inv: dict, layout: str, date_style: str, num_style: str, fake: Faker, rng: random.Random) -> str:
    s = inv["supplier"]
    n = lambda x: fmt_num(x, num_style)
    d = fmt_date(inv["invoice_date"], date_style)
    client = fake.company()                 # piège : ne pas confondre client et fournisseur
    rate_pct = int(round(inv["vat_rate"] * 100))
    due = rng.choice([15, 30, 45, 60])

    if layout == "classic_fr":
        rows = "\n".join(f"  {l['label']:<32} {l['qty']:>3}  x {n(l['unit']):>12}  = {n(l['amount']):>13}" for l in inv["lines"])
        return f"""{s['name']}
{fake.street_address()}, {s['city']}
Matricule fiscal : {s['tax_id']}

FACTURE N° {inv['invoice_number']}
Date : {d}

Client : {client}
{fake.street_address()}

Désignation                          Qté      P.U.            Montant
{rows}

Total HT ({CURRENCY})      : {n(inv['net_amount'])}
TVA {rate_pct} %             : {n(inv['vat_amount'])}
Total TTC ({CURRENCY})     : {n(inv['total_amount'])}

Conditions : paiement à {due} jours.
"""

    if layout == "modern_en":
        rows = "\n".join(f"- {l['label']} | qty {l['qty']} | unit {n(l['unit'])} | {n(l['amount'])}" for l in inv["lines"])
        return f"""INVOICE
Invoice no.: {inv['invoice_number']}
Issue date: {d}

From: {s['name']} ({s['city']}, Tunisia) - Tax ID {s['tax_id']}
Bill to: {client}

Items
{rows}

Subtotal: {n(inv['net_amount'])} {CURRENCY}
VAT ({rate_pct}%): {n(inv['vat_amount'])} {CURRENCY}
Amount due: {n(inv['total_amount'])} {CURRENCY}

Payment terms: net {due} days. Thank you for your business.
"""

    if layout == "compact":
        return (f"{s['name']} * {s['city']} * MF {s['tax_id']}\n"
                f"Fact. {inv['invoice_number']} du {d} -- Client: {client}\n"
                + " / ".join(f"{l['label']} ({l['qty']}) {n(l['amount'])}" for l in inv["lines"]) + "\n"
                f"Montant hors taxes {n(inv['net_amount'])} ; TVA {rate_pct}% {n(inv['vat_amount'])} ; "
                f"Net à payer {n(inv['total_amount'])} {CURRENCY}\n")

    # layout == "table_mixed" : libellés mélangés français/anglais, totaux en tableau
    rows = "\n".join(f"| {l['label']:<30} | {l['qty']:>3} | {n(l['amount']):>13} |" for l in inv["lines"])
    return f"""Facture / Invoice      Réf: {inv['invoice_number']}
Émise le / Issued on: {d}
Vendeur / Seller: {s['name']}, {s['city']}
Acheteur / Buyer: {client}

| Article                        | Qté |       Montant |
{rows}

| Base imposable / Net amount | {n(inv['net_amount'])} |
| Montant TVA / VAT amount    | {n(inv['vat_amount'])} |
| TOTAL TTC                   | {n(inv['total_amount'])} |
Devise : {CURRENCY}
"""


LAYOUTS = ["classic_fr", "modern_en", "compact", "table_mixed"]
DATE_STYLES = ["dmy_slash", "iso", "long_fr", "long_en"]
NUM_STYLES = ["fr", "en", "plain"]
LANG_OF_LAYOUT = {"classic_fr": "fr", "modern_en": "en", "compact": "fr", "table_mixed": "fr/en"}


# ---------------------------------------------------------------------------
# 6. ÉCRITURE DES FICHIERS
# ---------------------------------------------------------------------------

GT_FIELDS = ["invoice_id", "file", "invoice_number", "supplier_name", "invoice_date",
             "net_amount", "vat_amount", "total_amount", "vat_rate",
             "layout", "language", "date_format", "number_format",
             "is_inconsistent", "inconsistency_type", "anomaly_type"]


def write_text_set(name, n, n_inconsistent, n_anomalies, prefix, rng, fake, suppliers, out, history_ref):
    folder = out / name
    (folder / "invoices").mkdir(parents=True, exist_ok=True)

    special = rng.sample(range(n), n_inconsistent + n_anomalies)
    incons_idx, anom_idx = set(special[:n_inconsistent]), set(special[n_inconsistent:])

    rows, made = [], []
    for i in range(n):
        sup = rng.choice(suppliers)
        inv = make_invoice(rng, sup, random_date(rng, RECENT_START, RECENT_END), f"{prefix}-{2025}-{i + 1:04d}")
        if i in anom_idx:
            inject_anomaly(rng, inv, history_ref + made)
        if i in incons_idx:
            inject_inconsistency(rng, inv)
        made.append(inv)

        layout, ds, ns = LAYOUTS[i % 4], rng.choice(DATE_STYLES), NUM_STYLES[(i // 4) % 3]
        inv_id = f"{name}_{i + 1:03d}"
        fname = f"{inv_id}.txt"
        (folder / "invoices" / fname).write_text(render(inv, layout, ds, ns, fake, rng), encoding="utf-8")

        rows.append({
            "invoice_id": inv_id, "file": f"invoices/{fname}", "invoice_number": inv["invoice_number"],
            "supplier_name": inv["supplier_name"], "invoice_date": inv["invoice_date"].isoformat(),
            "net_amount": f"{inv['net_amount']:.3f}", "vat_amount": f"{inv['vat_amount']:.3f}",
            "total_amount": f"{inv['total_amount']:.3f}", "vat_rate": inv["vat_rate"],
            "layout": layout, "language": LANG_OF_LAYOUT[layout], "date_format": ds, "number_format": ns,
            "is_inconsistent": inv["is_inconsistent"], "inconsistency_type": inv["inconsistency_type"],
            "anomaly_type": inv["anomaly_type"],
        })

    with open(folder / "ground_truth.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=GT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def write_history(rng, suppliers, out):
    """Historique structuré (pas de texte, pas de LLM) pour entraîner Isolation Forest."""
    folder = out / "history"
    folder.mkdir(parents=True, exist_ok=True)
    invoices = []
    for i in range(N_HISTORY):
        sup = rng.choice(suppliers)
        inv = make_invoice(rng, sup, random_date(rng, HISTORY_START, HISTORY_END), f"H-{i + 1:05d}")
        if rng.random() < HISTORY_ANOMALY_RATE:
            inject_anomaly(rng, inv, invoices)
        invoices.append(inv)

    fields = ["invoice_number", "supplier_name", "invoice_date", "net_amount", "vat_amount",
              "total_amount", "vat_rate", "n_lines", "anomaly_type"]
    with open(folder / "history.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for inv in sorted(invoices, key=lambda x: x["invoice_date"]):
            w.writerow({
                "invoice_number": inv["invoice_number"], "supplier_name": inv["supplier_name"],
                "invoice_date": inv["invoice_date"].isoformat(),
                "net_amount": f"{inv['net_amount']:.3f}", "vat_amount": f"{inv['vat_amount']:.3f}",
                "total_amount": f"{inv['total_amount']:.3f}", "vat_rate": inv["vat_rate"],
                "n_lines": len(inv["lines"]), "anomaly_type": inv["anomaly_type"],
            })
    return invoices


# ---------------------------------------------------------------------------
# 7. PROGRAMME PRINCIPAL
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Générateur de factures synthétiques")
    parser.add_argument("--seed", type=int, default=42, help="graine aléatoire (reproductibilité)")
    parser.add_argument("--out", type=str, default="data", help="dossier de sortie")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    fake = Faker("fr_FR")
    Faker.seed(args.seed)
    out = Path(args.out)

    suppliers = build_suppliers(rng, fake)
    history = write_history(rng, suppliers, out)
    test_rows = write_text_set("test", N_TEST, N_TEST_INCONSISTENT, N_TEST_ANOMALIES, "F", rng, fake, suppliers, out, history)
    dev_rows = write_text_set("dev", N_DEV, N_DEV_INCONSISTENT, N_DEV_ANOMALIES, "D", rng, fake, suppliers, out, history)

    # Fiche descriptive du jeu de données (à citer dans le rapport : documentation et reproductibilité)
    manifest = {
        "seed": args.seed,
        "currency": CURRENCY,
        "vat_rates": VAT_RATES,
        "suppliers": [{k: v for k, v in s.items() if k != "unit_price_range"} for s in suppliers],
        "counts": {
            "test": len(test_rows), "dev": len(dev_rows), "history": len(history),
            "test_inconsistent": sum(r["is_inconsistent"] for r in test_rows),
            "test_anomalies": sum(bool(r["anomaly_type"]) for r in test_rows),
            "history_anomalies": sum(bool(i["anomaly_type"]) for i in history),
        },
        "fields_to_extract": ["supplier_name", "invoice_date", "net_amount", "vat_amount", "total_amount"],
        "ground_truth_rule": "Valeurs telles qu'imprimées sur la facture ; dates au format ISO ; montants avec 3 décimales.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    c = manifest["counts"]
    print(f"Données générées dans '{out}/' (graine {args.seed})")
    print(f"  test    : {c['test']} factures ({c['test_inconsistent']} incohérentes, {c['test_anomalies']} anomalies)")
    print(f"  dev     : {c['dev']} factures")
    print(f"  history : {c['history']} factures ({c['history_anomalies']} anomalies)")


if __name__ == "__main__":
    main()
