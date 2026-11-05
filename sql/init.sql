-- sql/init.sql — Initialisation de la base invoice_pe
-- À exécuter UNE SEULE FOIS en tant que superutilisateur postgres :
--
--   "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -f sql\init.sql
--
-- Ce script :
--   1. Crée la base invoice_pe
--   2. Crée les rôles invoice_loader et bi_readonly
--   3. Les mots de passe sont définis ici — changez-les avant d'exécuter,
--      puis synchronisez .env en conséquence.
--      Ne commitez JAMAIS ce fichier avec de vrais mots de passe.
--
-- Pour vérifier la version PostgreSQL installée :
--   "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "SELECT version();"
-- Pour lister les versions installées sur votre machine :
--   Get-ChildItem "C:\Program Files\PostgreSQL"  (dans PowerShell)

-- ---------------------------------------------------------------------------
-- 1. Base de données
-- ---------------------------------------------------------------------------
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'invoice_pe' AND pid <> pg_backend_pid();

DROP DATABASE IF EXISTS invoice_pe;
CREATE DATABASE invoice_pe
    ENCODING 'UTF8'
    LC_COLLATE 'French_Tunisia.1256'
    LC_CTYPE   'French_Tunisia.1256'
    TEMPLATE template0;

-- ---------------------------------------------------------------------------
-- 2. Rôles
-- ---------------------------------------------------------------------------

-- Rôle chargement : propriétaire des schémas, utilisé par load_db.py
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_loader') THEN
        CREATE ROLE invoice_loader LOGIN PASSWORD 'changeme_loader';
    ELSE
        ALTER ROLE invoice_loader WITH PASSWORD 'changeme_loader' LOGIN;
    END IF;
END$$;

-- Rôle lecture seule : utilisé par text2sql.py et pour toute requête LLM
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_readonly') THEN
        CREATE ROLE bi_readonly LOGIN
            PASSWORD 'changeme_readonly'
            CONNECTION LIMIT 10;
    ELSE
        ALTER ROLE bi_readonly WITH PASSWORD 'changeme_readonly' LOGIN;
    END IF;
END$$;

-- Paramètres de sécurité du rôle readonly (appliqués à la connexion)
ALTER ROLE bi_readonly SET default_transaction_read_only = on;
ALTER ROLE bi_readonly SET statement_timeout = '5s';

-- Accès à la base
GRANT CONNECT ON DATABASE invoice_pe TO invoice_loader;
GRANT CONNECT ON DATABASE invoice_pe TO bi_readonly;

-- invoice_loader doit pouvoir créer les schémas dans invoice_pe
GRANT CREATE ON DATABASE invoice_pe TO invoice_loader;
