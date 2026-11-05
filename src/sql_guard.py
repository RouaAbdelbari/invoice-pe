"""
sql_guard.py — Garde de sécurité SQL (Phase C2)

Vérifie qu'une requête générée par le LLM est sûre avant exécution.
Utilise sqlglot pour l'analyse syntaxique (dialecte PostgreSQL).

Règles appliquées (dans l'ordre) :
  1. Parseable  : la requête doit être syntaxiquement valide
  2. Single     : une seule instruction (pas de ; interne)
  3. Select     : uniquement SELECT ou WITH … SELECT (pas de DDL/DML/DCL)
  4. No danger  : pas de fonctions dangereuses (pg_sleep, pg_read_file, dblink,
                  lo_*, copy_*)
  5. No catalog : pas d'accès à pg_catalog ni information_schema
  6. Whitelist  : toutes les tables référencées appartiennent à la liste blanche
  7. Limit      : si LIMIT absent, en ajoute un (LIMIT 1000)

Retourne : (accepted: bool, rewritten_sql: str | None, reason: str)
  - accepted=True  : requête acceptée (éventuellement réécrite avec LIMIT)
  - accepted=False : requête refusée, reason explique pourquoi

Utilisation autonome :
    python src\\sql_guard.py "SELECT * FROM gold.fact_invoice"
"""

import re
import sys
from pathlib import Path

import sqlglot
import sqlglot.expressions as exp

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Tables autorisées — NOMS SANS SCHÉMA (le schéma est positionné via search_path)
# Les requêtes qualifiées (gold.x, pipeline.x, etc.) sont refusées avec le motif
# schema_qualified_table : le LLM ne doit pas connaître le schéma cible.
ALLOWED_TABLES = {
    "fact_invoice",
    "dim_supplier",
    "dim_date",
    "dim_vat",
    "rejected_invoice",
    "load_info",
}

# Schémas dont la qualification est explicitement interdite dans les requêtes LLM
FORBIDDEN_QUALIFIER_SCHEMAS = {"gold", "pipeline", "public", "history",
                                "pg_catalog", "information_schema"}

# Fonctions interdites (en minuscules, correspondance exacte sur le nom de la fonction)
DANGEROUS_FUNCTIONS = {
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "pg_stat_file", "dblink", "dblink_exec", "lo_import", "lo_export",
    "lo_create", "lo_unlink", "copy_from", "copy_to",
    "pg_execute_server_program", "pg_write_file",
}

# Schémas/tables système interdits
FORBIDDEN_SCHEMAS = {"pg_catalog", "information_schema"}

LIMIT_MAX = 1000

# ---------------------------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------------------------

def _normalize(sql: str) -> str:
    """Retire les commentaires et normalise les espaces."""
    # Commentaires SQL --…
    sql = re.sub(r"--[^\n]*", " ", sql)
    # Commentaires /* … */
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql.strip()


def _count_statements(sql: str) -> int:
    """Compte les instructions séparées par ';' (hors celles vides)."""
    parts = [p.strip() for p in sql.split(";")]
    return sum(1 for p in parts if p)


def _is_select_tree(tree) -> bool:
    """Vérifie que la racine est un SELECT ou WITH…SELECT."""
    if isinstance(tree, (exp.Select,)):
        return True
    if isinstance(tree, exp.With):
        return isinstance(tree.expression, exp.Select)
    # sqlglot peut encapsuler dans un Union
    if isinstance(tree, exp.Union):
        return True
    return False


def _collect_table_refs(tree) -> set[str]:
    """Retourne l'ensemble des noms de tables référencées (schéma.table en minuscules)."""
    refs = set()
    for node in tree.walk():
        if isinstance(node, exp.Table):
            schema_obj = node.args.get("db")
            name = node.name or ""
            if schema_obj is not None:
                # Dans les versions récentes de sqlglot, db est un Identifier
                schema_str = (schema_obj.name if hasattr(schema_obj, "name")
                              else str(schema_obj))
                refs.add(f"{schema_str.lower()}.{name.lower()}")
            elif name:
                refs.add(name.lower())
    return refs


def _collect_functions(tree) -> set[str]:
    """Retourne l'ensemble des noms de fonctions appelées (en minuscules)."""
    funcs = set()
    for node in tree.walk():
        if isinstance(node, (exp.Anonymous, exp.Func)):
            fname = getattr(node, "name", None) or getattr(node, "sql_name", lambda: "")()
            if fname:
                funcs.add(fname.lower())
    return funcs


def _has_limit(tree) -> bool:
    """Vérifie si la requête contient déjà un LIMIT."""
    for node in tree.walk():
        if isinstance(node, exp.Limit):
            return True
    return False


def _add_limit(sql: str, limit: int) -> str:
    """Ajoute LIMIT <limit> à la fin de la requête (avant le ';' final s'il y en a un)."""
    sql = sql.rstrip().rstrip(";").rstrip()
    return f"{sql}\nLIMIT {limit}"


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

def check(sql: str) -> tuple[bool, str | None, str]:
    """Vérifie et éventuellement réécrit la requête SQL.

    Returns:
        (accepted, rewritten_sql, reason)
        - accepted=True  : rewritten_sql est la requête à exécuter
        - accepted=False : rewritten_sql=None, reason explique le refus
    """
    sql = _normalize(sql)
    if not sql:
        return False, None, "Requête vide"

    # Règle 2 : une seule instruction
    if _count_statements(sql) > 1:
        return False, None, "Plusieurs instructions détectées (séparateur ';') — une seule instruction autorisée"

    # Règle 1 : parseable
    try:
        trees = sqlglot.parse(sql, dialect="postgres")
    except Exception as e:
        return False, None, f"Erreur de syntaxe SQL : {e}"

    if not trees or trees[0] is None:
        return False, None, "Impossible de parser la requête"

    tree = trees[0]

    # Règle 3 : SELECT uniquement
    # On vérifie aussi via le texte pour les instructions DDL/DML qui pourraient passer
    sql_upper = sql.upper().lstrip()
    forbidden_starts = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
                        "TRUNCATE", "GRANT", "REVOKE", "COPY", "EXECUTE", "CALL",
                        "VACUUM", "ANALYZE", "COMMENT", "LOCK")
    for kw in forbidden_starts:
        if sql_upper.startswith(kw):
            return False, None, f"Instruction interdite : {kw} — seul SELECT est autorisé"

    if not _is_select_tree(tree):
        kind = type(tree).__name__
        return False, None, f"Instruction non SELECT ({kind}) — seul SELECT est autorisé"

    # Règle 4 : fonctions dangereuses
    funcs = _collect_functions(tree)
    dangerous_found = funcs & DANGEROUS_FUNCTIONS
    if dangerous_found:
        return False, None, f"Fonction(s) interdite(s) : {', '.join(sorted(dangerous_found))}"

    # Règle 5 : pas de pg_catalog / information_schema, et pas de qualification par schéma
    table_refs = _collect_table_refs(tree)
    for ref in table_refs:
        if "." in ref:
            schema_part = ref.split(".")[0]
            if schema_part in FORBIDDEN_QUALIFIER_SCHEMAS:
                return False, None, (
                    f"schema_qualified_table : table qualifiée interdite '{ref}'. "
                    f"Écris les noms de tables sans schéma (le search_path est positionné automatiquement)."
                )
            # Schéma qualifié inconnu → aussi refusé
            return False, None, (
                f"schema_qualified_table : qualification de schéma interdite dans '{ref}'."
            )

    # Détection textuelle pg_catalog / information_schema (backup)
    sql_lower = sql.lower()
    for forbidden_schema in FORBIDDEN_SCHEMAS:
        if forbidden_schema in sql_lower:
            return False, None, f"Référence interdite à {forbidden_schema} détectée dans la requête"

    # Règle 6 : liste blanche — noms sans schéma uniquement
    cte_names = set()
    for node in tree.walk():
        if isinstance(node, exp.CTE):
            alias = node.alias or ""
            cte_names.add(alias.lower())

    not_allowed = set()
    for ref in table_refs:
        if ref in cte_names:
            continue
        # À ce stade, toutes les refs qualifiées ont été bloquées en règle 5
        # On vérifie les noms non qualifiés contre la liste blanche
        name_only = ref.split(".")[-1] if "." in ref else ref
        if name_only not in ALLOWED_TABLES:
            not_allowed.add(ref)

    if not_allowed:
        return False, None, (
            f"Table(s) non autorisée(s) : {', '.join(sorted(not_allowed))}. "
            f"Tables autorisées : {', '.join(sorted(ALLOWED_TABLES))}"
        )

    # Règle 7 : LIMIT
    rewritten = sql
    limit_added = False
    if not _has_limit(tree):
        rewritten = _add_limit(sql, LIMIT_MAX)
        limit_added = True

    reason = "OK" + (" (LIMIT 1000 ajouté)" if limit_added else "")
    return True, rewritten, reason


# ---------------------------------------------------------------------------
# CLI minimal
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python src\\sql_guard.py \"<requête SQL>\"")
        sys.exit(1)
    sql_input = " ".join(sys.argv[1:])
    accepted, rewritten, reason = check(sql_input)
    print(f"Accepté : {accepted}")
    print(f"Raison  : {reason}")
    if accepted:
        print(f"Requête :\n{rewritten}")
