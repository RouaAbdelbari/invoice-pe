"""
validate.py — Contrôles déterministes appliqués à la sortie du LLM (étape 1)

Le LLM ne fait QUE lire la facture. Tout ce qui suit est du code Python déterministe,
identique pour toutes les versions de prompt :
  1. analyse du JSON (strict puis tolérant)
  2. normalisation des dates (-> ISO) et des montants (-> float)
  3. contrôles : champs obligatoires, types, montants positifs,
     cohérence HT + TVA = TTC, taux de TVA plausible

CHANGELOG validate.py
  2026-11-04  normalize_amount : refonte de la logique de désambiguïsation des séparateurs
              pour couvrir correctement tous les formats string attendus :
              "2 988,529" (fr), "4,309.664" (en, 3 déc.), "1 234 567,890" (fr grands montants),
              "1.234.567,890" (points milliers + virgule décimale).
              Les valeurs numériques (int/float) sont retournées sans modification.
              Aucun contrôle de plausibilité des montants ajouté (décision expérimentale réservée).
"""

import json
import re
from datetime import date

REQUIRED_FIELDS = ["supplier_name", "invoice_date", "net_amount", "vat_amount", "total_amount"]
AMOUNT_FIELDS = ["net_amount", "vat_amount", "total_amount"]
VAT_RATES = [0.07, 0.13, 0.19]
TOLERANCE = 0.01          # tolérance arithmétique (en dinars)
RATE_TOLERANCE = 0.005    # tolérance sur le taux de TVA (0,5 point)

MONTHS = {
    # français
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12, "decembre": 12,
    # anglais
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


# ---------------------------------------------------------------------------
# 1. Analyse du JSON
# ---------------------------------------------------------------------------

def parse_json(raw: str) -> tuple[dict | None, bool, bool]:
    """Renvoie (objet, json_strict, json_lenient).
    - json_strict  : la réponse entière est un JSON valide (après suppression des espaces)
    - json_lenient : un objet JSON a pu être extrait du texte (balises ```json, texte autour...)"""
    text = raw.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj, True, True
    except json.JSONDecodeError:
        pass
    # Mode tolérant : on retire les balises markdown et on cherche le premier bloc {...}
    cleaned = re.sub(r"```(?:json)?", "", text)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj, False, True
        except json.JSONDecodeError:
            pass
    return None, False, False


# ---------------------------------------------------------------------------
# 2. Normalisation
# ---------------------------------------------------------------------------

def normalize_amount(value) -> float | None:
    """Convertit une valeur montant en float.

    Formats string supportés (dinar tunisien, 3 décimales) :
      "2 988,529"       -> 2988.529   (espace milliers + virgule décimale, fr)
      "4,309.664"       -> 4309.664   (virgule milliers + point décimal, en, 3 déc.)
      "4309.664"        -> 4309.664   (plain, point décimal)
      "1 234 567,890"   -> 1234567.890 (espaces milliers + virgule décimale, fr)
      "1.234.567,890"   -> 1234567.890 (points milliers + virgule décimale)
      "1,234,567.890"   -> 1234567.890 (virgules milliers + point décimal, en)
      "1234567.890"     -> 1234567.890 (plain)
      "1 234.567 TND"   -> 1234.567   (avec unité monétaire)

    Valeurs numériques : retournées sans modification (pas de correction ×1000).
      2988529   -> 2988529.0
      2988.529  -> 2988.529
    """
    if value is None or isinstance(value, bool):
        return None
    # Valeurs numériques : retour direct, sans correction
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    # Retirer les unités monétaires et les espaces insécables
    s = s.replace("\u00a0", " ").replace("\u202f", " ")
    s = re.sub(r"[A-Za-z]+$", "", s).strip()   # retire "TND", "EUR", etc. en fin de chaîne
    s = re.sub(r"^[A-Za-z]+", "", s).strip()   # retire éventuellement en début

    if not s or not re.search(r"\d", s):
        return None

    # Compter les occurrences de chaque séparateur dans la partie numérique brute
    n_comma = s.count(",")
    n_dot = s.count(".")
    n_space = len(re.findall(r" ", s))

    # --- Cas 1 : plusieurs virgules → virgules = milliers ---
    #     ex. "1,234,567.890" ou "1,234,567"
    if n_comma > 1:
        s = s.replace(",", "")
        # Si un point reste, c'est le séparateur décimal → float(s) le gère
        try:
            return float(s.replace(" ", ""))
        except ValueError:
            return None

    # --- Cas 2 : plusieurs points → points = milliers ---
    #     ex. "1.234.567,890" ou "1.234.567"
    if n_dot > 1:
        s = s.replace(".", "")
        # Si une virgule reste, c'est le séparateur décimal
        s = s.replace(",", ".").replace(" ", "")
        try:
            return float(s)
        except ValueError:
            return None

    # --- Cas 3 : virgule ET point (chacun une seule fois) ---
    #     Le dernier séparateur est le décimal.
    #     ex. "4,309.664" → dernier est "." → point décimal, virgule = milliers
    #     ex. "1 234,567" avec un point n'arrive pas ici (n_space > 0 mais n_dot = 0)
    if n_comma == 1 and n_dot == 1:
        last_comma = s.rfind(",")
        last_dot = s.rfind(".")
        if last_dot > last_comma:
            # point décimal, virgule = milliers  →  "4,309.664"
            s = s.replace(",", "").replace(" ", "")
        else:
            # virgule décimale, point = milliers  →  rare mais possible
            s = s.replace(".", "").replace(",", ".").replace(" ", "")
        try:
            return float(s)
        except ValueError:
            return None

    # --- Cas 4 : virgule seule (zéro ou un seul point) ---
    #     ex. "2 988,529" → virgule = décimal (format fr)
    #     ex. "1 234 567,890"
    if n_comma == 1 and n_dot == 0:
        s = s.replace(" ", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None

    # --- Cas 5 : point seul ---
    #     ex. "4309.664", "1234.567", "20610.44"
    if n_dot == 1 and n_comma == 0:
        s = s.replace(" ", "")
        try:
            return float(s)
        except ValueError:
            return None

    # --- Cas 6 : aucun séparateur ou espaces seuls (entier) ---
    s = s.replace(" ", "").replace(",", "").replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_date(value) -> str | None:
    """Convertit '28/08/2025', '2025-08-28', '28 août 2025', 'August 28, 2025' -> '2025-08-28'"""
    if not value or not isinstance(value, str):
        return None
    s = value.strip().lower().replace(",", " ")
    try:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", s)
        if m:
            return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
        m = re.fullmatch(r"(\d{1,2})\s+([a-zéû]+)\s+(\d{4})", s)          # 28 août 2025
        if m and m[2] in MONTHS:
            return date(int(m[3]), MONTHS[m[2]], int(m[1])).isoformat()
        m = re.fullmatch(r"([a-zéû]+)\s+(\d{1,2})\s+(\d{4})", s)          # august 28 2025
        if m and m[1] in MONTHS:
            return date(int(m[3]), MONTHS[m[1]], int(m[2])).isoformat()
    except ValueError:           # date impossible, ex. 31/02/2025
        return None
    return None


# ---------------------------------------------------------------------------
# 3. Validation
# ---------------------------------------------------------------------------

def validate(obj: dict | None) -> dict:
    """Renvoie un dictionnaire avec les valeurs normalisées et le résultat de chaque contrôle."""
    result = {
        "schema_ok": False,        # les 5 clés sont présentes
        "required_ok": False,      # aucune valeur vide
        "types_ok": False,         # date et montants convertibles
        "arithmetic_ok": None,     # HT + TVA = TTC (None si non vérifiable)
        "vat_rate_ok": None,       # TVA / HT proche d'un taux légal
        "is_valid": False,         # tous les contrôles passent
        "errors": [],
        "supplier_name": None, "invoice_date": None,
        "net_amount": None, "vat_amount": None, "total_amount": None,
    }
    if obj is None:
        result["errors"].append("json_invalide")
        return result

    missing = [k for k in REQUIRED_FIELDS if k not in obj]
    result["schema_ok"] = not missing
    if missing:
        result["errors"].append("cles_manquantes:" + "|".join(missing))

    empty = [k for k in REQUIRED_FIELDS if k in obj and obj[k] in (None, "", [], {})]
    result["required_ok"] = result["schema_ok"] and not empty
    if empty:
        result["errors"].append("valeurs_vides:" + "|".join(empty))

    # Normalisation
    name = obj.get("supplier_name")
    result["supplier_name"] = name.strip() if isinstance(name, str) and name.strip() else None
    result["invoice_date"] = normalize_date(obj.get("invoice_date"))
    for k in AMOUNT_FIELDS:
        result[k] = normalize_amount(obj.get(k))

    bad_types = [k for k in REQUIRED_FIELDS if result[k] is None and k in obj and k not in empty]
    result["types_ok"] = result["required_ok"] and not bad_types
    if bad_types:
        result["errors"].append("types_invalides:" + "|".join(bad_types))

    net, vat, total = (result[k] for k in AMOUNT_FIELDS)
    if None not in (net, vat, total):
        if min(net, vat, total) < 0:
            result["errors"].append("montant_negatif")
        result["arithmetic_ok"] = abs(net + vat - total) <= TOLERANCE
        if not result["arithmetic_ok"]:
            result["errors"].append("HT+TVA!=TTC")
        if net > 0:
            rate = vat / net
            result["vat_rate_ok"] = any(abs(rate - r) <= RATE_TOLERANCE for r in VAT_RATES)
            if not result["vat_rate_ok"]:
                result["errors"].append(f"taux_TVA_inhabituel:{rate:.3f}")

    result["is_valid"] = (result["types_ok"] and result["arithmetic_ok"] is True
                          and result["vat_rate_ok"] is True and "montant_negatif" not in result["errors"])
    return result
