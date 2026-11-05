"""
db.py — Module de connexion à PostgreSQL

Fournit deux fonctions publiques :
  connect_loader()   → connexion avec le rôle invoice_loader (écriture)
  connect_readonly() → connexion avec le rôle bi_readonly (lecture seule)

Les identifiants sont lus depuis .env (à la racine du projet invoice-pe/).
Aucun autre fichier ne doit lire les variables PG_* directement.

Variables attendues dans .env :
  PG_HOST, PG_PORT, PG_DATABASE,
  PG_LOADER_USER, PG_LOADER_PASSWORD,
  PG_READONLY_USER, PG_READONLY_PASSWORD

En cas d'absence ou d'incomplétude de .env, un message clair est affiché
et SystemExit est levé.
"""

import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

# Cherche .env à la racine du projet (deux niveaux au-dessus de src/)
_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _ROOT / ".env"

# Chargement unique au moment de l'import
_loaded = load_dotenv(_ENV_PATH, override=False)

_REQUIRED_BASE = ["PG_HOST", "PG_PORT", "PG_DATABASE"]
_REQUIRED_LOADER = _REQUIRED_BASE + ["PG_LOADER_USER", "PG_LOADER_PASSWORD"]
_REQUIRED_READONLY = _REQUIRED_BASE + ["PG_READONLY_USER", "PG_READONLY_PASSWORD"]


def _check_env(required: list[str]) -> dict[str, str]:
    """Vérifie que toutes les variables requises sont définies.
    Lève SystemExit avec un message explicite sinon."""
    if not _ENV_PATH.exists():
        raise SystemExit(
            f"Fichier .env introuvable : {_ENV_PATH}\n"
            f"Copiez .env.example en .env et remplissez les valeurs.\n"
            f"  copy .env.example .env"
        )
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"Variables manquantes dans {_ENV_PATH} :\n"
            + "\n".join(f"  {k}" for k in missing)
            + f"\nConsultez .env.example pour la liste complète."
        )
    return {k: os.environ[k] for k in required}


def connect_loader() -> psycopg2.extensions.connection:
    """Retourne une connexion psycopg2 avec le rôle invoice_loader.

    Ce rôle est propriétaire des schémas gold, pipeline et history.
    Utilisé exclusivement par load_db.py.
    """
    env = _check_env(_REQUIRED_LOADER)
    try:
        conn = psycopg2.connect(
            host=env["PG_HOST"],
            port=int(env["PG_PORT"]),
            dbname=env["PG_DATABASE"],
            user=env["PG_LOADER_USER"],
            password=env["PG_LOADER_PASSWORD"],
        )
        return conn
    except psycopg2.OperationalError as e:
        raise SystemExit(
            f"Connexion PostgreSQL impossible (rôle loader) :\n  {e}\n"
            f"Vérifiez que PostgreSQL tourne et que les identifiants dans .env sont corrects.\n"
            f"  PG_HOST={env['PG_HOST']}  PG_PORT={env['PG_PORT']}  "
            f"PG_DATABASE={env['PG_DATABASE']}  PG_LOADER_USER={env['PG_LOADER_USER']}"
        )


def connect_readonly() -> psycopg2.extensions.connection:
    """Retourne une connexion psycopg2 avec le rôle bi_readonly.

    Ce rôle est en lecture seule (default_transaction_read_only = on,
    statement_timeout = 5s). Utilisé par text2sql.py et sql_guard.py.
    """
    env = _check_env(_REQUIRED_READONLY)
    try:
        conn = psycopg2.connect(
            host=env["PG_HOST"],
            port=int(env["PG_PORT"]),
            dbname=env["PG_DATABASE"],
            user=env["PG_READONLY_USER"],
            password=env["PG_READONLY_PASSWORD"],
        )
        return conn
    except psycopg2.OperationalError as e:
        raise SystemExit(
            f"Connexion PostgreSQL impossible (rôle readonly) :\n  {e}\n"
            f"Vérifiez que PostgreSQL tourne et que les identifiants dans .env sont corrects.\n"
            f"  PG_HOST={env['PG_HOST']}  PG_PORT={env['PG_PORT']}  "
            f"PG_DATABASE={env['PG_DATABASE']}  PG_READONLY_USER={env['PG_READONLY_USER']}"
        )
