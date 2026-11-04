"""
tests/test_validate.py — Tests unitaires pour src/validate.py

Couvre (spécification A3) :
  - parse_json : JSON strict, balises ```, texte autour, JSON invalide, clé manquante
  - normalize_amount : formats fr/en/plain, 3 décimales, grands montants, entrées
    numériques (int/float renvoyés sans modification, jamais corrigés ×1000)
  - normalize_date : formats fr/en, ISO, jj/mm/aaaa, date impossible
  - validate : champs manquants, valeurs vides, types invalides, cohérence
    arithmétique, taux de TVA, montant négatif, facture incohérente (valid=False)

Lancer depuis invoice-pe/ :
    python -m pytest tests/ -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from validate import normalize_amount, normalize_date, parse_json, validate


# ===========================================================================
# parse_json
# ===========================================================================

class TestParseJson:

    def test_strict_clean_json(self):
        raw = '{"supplier_name": "Acme", "invoice_date": "2025-01-01", ' \
              '"net_amount": 100.0, "vat_amount": 19.0, "total_amount": 119.0}'
        obj, strict, lenient = parse_json(raw)
        assert strict is True
        assert lenient is True
        assert obj["supplier_name"] == "Acme"

    def test_lenient_backtick_json(self):
        raw = '```json\n{"supplier_name": "Acme", "invoice_date": "2025-01-01", ' \
              '"net_amount": 100.0, "vat_amount": 19.0, "total_amount": 119.0}\n```'
        obj, strict, lenient = parse_json(raw)
        assert strict is False
        assert lenient is True
        assert obj is not None

    def test_lenient_text_around_json(self):
        raw = 'Voici le résultat :\n{"supplier_name": "Acme", "invoice_date": "2025-01-01", ' \
              '"net_amount": 100.0, "vat_amount": 19.0, "total_amount": 119.0}\nMerci.'
        obj, strict, lenient = parse_json(raw)
        assert strict is False
        assert lenient is True

    def test_invalid_json(self):
        raw = "Désolé, je ne comprends pas."
        obj, strict, lenient = parse_json(raw)
        assert obj is None
        assert strict is False
        assert lenient is False

    def test_missing_key_still_parses(self):
        """parse_json extrait l'objet même s'il manque des clés."""
        raw = '{"supplier_name": "Acme"}'
        obj, strict, lenient = parse_json(raw)
        assert obj is not None
        assert "invoice_date" not in obj

    def test_backtick_without_json_label(self):
        raw = '```\n{"supplier_name": "Test", "invoice_date": "2025-03-01", ' \
              '"net_amount": 50.0, "vat_amount": 9.5, "total_amount": 59.5}\n```'
        obj, strict, lenient = parse_json(raw)
        assert lenient is True
        assert obj is not None

    def test_empty_string(self):
        obj, strict, lenient = parse_json("")
        assert obj is None
        assert strict is False
        assert lenient is False


# ===========================================================================
# normalize_amount
# ===========================================================================

class TestNormalizeAmount:

    # --- Formats string (dinars tunisiens, 3 décimales) ---

    def test_fr_space_comma(self):
        """Format français : espace milliers + virgule décimale."""
        assert normalize_amount("2 988,529") == pytest.approx(2988.529)

    def test_fr_space_comma_large(self):
        """Format français grand montant."""
        assert normalize_amount("1 234 567,890") == pytest.approx(1234567.890)

    def test_en_comma_dot_3dec(self):
        """Format anglais : virgule milliers + point décimal, 3 décimales."""
        assert normalize_amount("4,309.664") == pytest.approx(4309.664)

    def test_en_comma_dot_large(self):
        """Format anglais grand montant."""
        assert normalize_amount("1,234,567.890") == pytest.approx(1234567.890)

    def test_plain_dot(self):
        """Format plain : point décimal, pas de séparateur de milliers."""
        assert normalize_amount("4309.664") == pytest.approx(4309.664)

    def test_plain_no_decimal(self):
        assert normalize_amount("1234567") == pytest.approx(1234567.0)

    def test_dots_as_thousands_comma_decimal(self):
        """Points = milliers, virgule = décimal."""
        assert normalize_amount("1.234.567,890") == pytest.approx(1234567.890)

    def test_with_currency_suffix(self):
        """Unité monétaire en fin de chaîne."""
        assert normalize_amount("1 234,567 TND") == pytest.approx(1234.567)

    def test_small_fr(self):
        assert normalize_amount("88,500") == pytest.approx(88.500)

    def test_small_en(self):
        assert normalize_amount("88.500") == pytest.approx(88.500)

    def test_fr_two_space_groups(self):
        assert normalize_amount("20 610,440") == pytest.approx(20610.440)

    def test_en_with_spaces_and_dot(self):
        assert normalize_amount("20 610.440") == pytest.approx(20610.440)

    # --- Valeurs numériques : retour sans modification (jamais corrigé ×1000) ---

    def test_int_returned_as_float(self):
        """Un entier est retourné tel quel — PAS de correction ×1000."""
        assert normalize_amount(2988529) == pytest.approx(2988529.0)

    def test_float_returned_unchanged(self):
        assert normalize_amount(2988.529) == pytest.approx(2988.529)

    def test_float_large_unchanged(self):
        assert normalize_amount(4309664.0) == pytest.approx(4309664.0)

    def test_int_zero(self):
        assert normalize_amount(0) == pytest.approx(0.0)

    def test_float_small(self):
        assert normalize_amount(88.5) == pytest.approx(88.5)

    # --- Cas limites ---

    def test_none_returns_none(self):
        assert normalize_amount(None) is None

    def test_bool_returns_none(self):
        assert normalize_amount(True) is None
        assert normalize_amount(False) is None

    def test_empty_string_returns_none(self):
        assert normalize_amount("") is None

    def test_text_only_returns_none(self):
        assert normalize_amount("abc") is None

    def test_negative_amount(self):
        assert normalize_amount("-100,000") == pytest.approx(-100.0)


# ===========================================================================
# normalize_date
# ===========================================================================

class TestNormalizeDate:

    def test_iso_date(self):
        assert normalize_date("2025-08-28") == "2025-08-28"

    def test_iso_date_single_digit(self):
        assert normalize_date("2025-8-3") == "2025-08-03"

    def test_dmy_slash(self):
        assert normalize_date("28/08/2025") == "2025-08-28"

    def test_dmy_dot(self):
        assert normalize_date("28.08.2025") == "2025-08-28"

    def test_dmy_dash(self):
        assert normalize_date("28-08-2025") == "2025-08-28"

    def test_long_fr_standard(self):
        assert normalize_date("28 août 2025") == "2025-08-28"

    def test_long_fr_accent_variant(self):
        assert normalize_date("12 février 2025") == "2025-02-12"

    def test_long_fr_no_accent(self):
        assert normalize_date("12 fevrier 2025") == "2025-02-12"

    def test_long_en(self):
        assert normalize_date("August 28, 2025") == "2025-08-28"

    def test_long_en_no_comma(self):
        assert normalize_date("March 12 2025") == "2025-03-12"

    def test_long_en_january(self):
        assert normalize_date("January 1, 2025") == "2025-01-01"

    def test_long_en_december(self):
        assert normalize_date("December 31, 2025") == "2025-12-31"

    def test_impossible_date_returns_none(self):
        assert normalize_date("31/02/2025") is None

    def test_none_returns_none(self):
        assert normalize_date(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_date("") is None

    def test_garbage_returns_none(self):
        assert normalize_date("pas une date") is None


# ===========================================================================
# validate (intégration)
# ===========================================================================

class TestValidate:

    def _ok_obj(self, **overrides):
        base = {
            "supplier_name": "Acme SARL",
            "invoice_date": "2025-08-28",
            "net_amount": "1000,000",
            "vat_amount": "190,000",
            "total_amount": "1190,000",
        }
        base.update(overrides)
        return base

    def test_valid_invoice_fr_format(self):
        r = validate(self._ok_obj())
        assert r["is_valid"] is True
        assert r["errors"] == []
        assert r["net_amount"] == pytest.approx(1000.0)

    def test_valid_invoice_en_format(self):
        obj = self._ok_obj(net_amount="1,000.000", vat_amount="190.000",
                           total_amount="1,190.000")
        r = validate(obj)
        assert r["is_valid"] is True

    def test_valid_invoice_plain_format(self):
        obj = self._ok_obj(net_amount="1000.000", vat_amount="190.000",
                           total_amount="1190.000")
        r = validate(obj)
        assert r["is_valid"] is True

    def test_none_obj_returns_json_invalide(self):
        r = validate(None)
        assert r["is_valid"] is False
        assert "json_invalide" in r["errors"]

    def test_missing_keys(self):
        r = validate({"supplier_name": "Acme"})
        assert r["is_valid"] is False
        assert r["schema_ok"] is False
        assert any("cles_manquantes" in e for e in r["errors"])

    def test_empty_value(self):
        r = validate(self._ok_obj(supplier_name=""))
        assert r["is_valid"] is False
        assert any("valeurs_vides" in e for e in r["errors"])

    def test_empty_amount(self):
        r = validate(self._ok_obj(net_amount=None))
        assert r["is_valid"] is False

    def test_invalid_date_type(self):
        r = validate(self._ok_obj(invoice_date="pas-une-date"))
        assert r["is_valid"] is False
        assert any("types_invalides" in e for e in r["errors"])

    def test_invalid_amount_string(self):
        r = validate(self._ok_obj(net_amount="abc"))
        assert r["is_valid"] is False
        assert any("types_invalides" in e for e in r["errors"])

    def test_arithmetic_wrong_total(self):
        """Facture incohérente (wrong_total) : is_valid=False, HT+TVA!=TTC détecté."""
        r = validate(self._ok_obj(total_amount="1500,000"))
        assert r["is_valid"] is False
        assert r["arithmetic_ok"] is False
        assert "HT+TVA!=TTC" in r["errors"]

    def test_arithmetic_wrong_vat(self):
        """Facture incohérente (wrong_vat) : TVA modifiée, TTC recalculé sur mauvaise TVA."""
        # net=1000, vat=250 (au lieu de 190), total=1250 → HT+TVA=TTC mais taux anormal
        r = validate(self._ok_obj(vat_amount="250,000", total_amount="1250,000"))
        assert r["is_valid"] is False
        assert r["arithmetic_ok"] is True           # HT+TVA=TTC est cohérent
        assert r["vat_rate_ok"] is False            # mais taux = 25% → inhabituel
        assert any("taux_TVA_inhabituel" in e for e in r["errors"])

    def test_vat_rate_19_pct(self):
        r = validate(self._ok_obj(
            net_amount="1000.000", vat_amount="190.000", total_amount="1190.000"))
        assert r["vat_rate_ok"] is True

    def test_vat_rate_13_pct(self):
        r = validate(self._ok_obj(
            net_amount="1000.000", vat_amount="130.000", total_amount="1130.000"))
        assert r["vat_rate_ok"] is True

    def test_vat_rate_7_pct(self):
        r = validate(self._ok_obj(
            net_amount="1000.000", vat_amount="70.000", total_amount="1070.000"))
        assert r["vat_rate_ok"] is True

    def test_negative_amount_flagged(self):
        r = validate(self._ok_obj(
            net_amount="-1000.000", vat_amount="-190.000", total_amount="-1190.000"))
        assert r["is_valid"] is False
        assert "montant_negatif" in r["errors"]

    def test_numeric_amounts_pass_through(self):
        """Les montants numériques (int/float) ne sont pas corrigés ×1000."""
        r = validate(self._ok_obj(
            net_amount=1000.0, vat_amount=190.0, total_amount=1190.0))
        assert r["net_amount"] == pytest.approx(1000.0)
        assert r["is_valid"] is True

    def test_amount_x1000_not_corrected(self):
        """2988529 (entier LLM) passe sans correction — arithmetic échoue ou taux invalide."""
        r = validate({
            "supplier_name": "Acme",
            "invoice_date": "2025-08-28",
            "net_amount": 2988529,
            "vat_amount": 567821,
            "total_amount": 3556350,
        })
        # Les montants sont retournés tels quels
        assert r["net_amount"] == pytest.approx(2988529.0)
        # is_valid peut être True ou False selon l'arithmétique, mais pas corrigé
        assert r["net_amount"] != pytest.approx(2988.529)

    def test_supplier_name_stripped(self):
        r = validate(self._ok_obj(supplier_name="  Acme SARL  "))
        assert r["supplier_name"] == "Acme SARL"

    def test_date_long_fr_normalized(self):
        r = validate(self._ok_obj(invoice_date="28 août 2025"))
        assert r["invoice_date"] == "2025-08-28"

    def test_date_dmy_slash_normalized(self):
        r = validate(self._ok_obj(invoice_date="28/08/2025"))
        assert r["invoice_date"] == "2025-08-28"

    def test_date_long_en_normalized(self):
        r = validate(self._ok_obj(invoice_date="August 28, 2025"))
        assert r["invoice_date"] == "2025-08-28"

    def test_json_with_backticks_via_parse_then_validate(self):
        """Chaîne complète parse_json → validate sur une réponse LLM typique."""
        raw = ('```json\n'
               '{"supplier_name": "Acme", "invoice_date": "2025-08-28", '
               '"net_amount": "1 000,000", "vat_amount": "190,000", '
               '"total_amount": "1 190,000"}\n```')
        obj, strict, lenient = parse_json(raw)
        assert lenient is True
        r = validate(obj)
        assert r["net_amount"] == pytest.approx(1000.0)
        assert r["is_valid"] is True

    def test_vat_arithmetic_tolerance(self):
        """Tolérance de 1 centime sur HT+TVA=TTC."""
        # total = 1190.009 → écart = 0.009 < 0.01 → ok
        r = validate(self._ok_obj(
            net_amount="1000.000", vat_amount="190.000", total_amount="1190.009"))
        assert r["arithmetic_ok"] is True

    def test_vat_arithmetic_over_tolerance(self):
        r = validate(self._ok_obj(
            net_amount="1000.000", vat_amount="190.000", total_amount="1190.020"))
        assert r["arithmetic_ok"] is False
