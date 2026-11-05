-- sql/schema.sql — Modèle en étoile invoice_pe
-- Exécuté par load_db.py (idempotent — CREATE IF NOT EXISTS partout).
-- Peut aussi être appliqué manuellement :
--   "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U invoice_loader -d invoice_pe -f sql\schema.sql
--
-- Trois schémas :
--   gold      : sortie d'un pipeline parfait.
--               fact_invoice  = factures is_inconsistent=False (vraies valeurs).
--               rejected_invoice = factures is_inconsistent=True (motif = type d'incohérence).
--   pipeline  : sortie du pipeline LLM.
--               fact_invoice  = fiches is_valid=True.
--               rejected_invoice = fiches is_valid=False (motif = erreurs de validation).
--               load_info     = traçabilité de chaque chargement.
--   history   : historique structuré pour Isolation Forest (table plate).
--
-- Les schémas gold et pipeline ont exactement les mêmes noms de tables et de colonnes :
-- les mêmes requêtes SQL tournent indifféremment sur l'un ou l'autre.
--
-- NUMERIC(14,3) pour tous les montants (dinar tunisien, 3 décimales).

-- ===========================================================================
-- SCHÉMA gold
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS gold AUTHORIZATION invoice_loader;

-- Dimension fournisseur
-- Note : aucune normalisation des noms. Un nom extrait différent du nom gold
-- crée une nouvelle entrée dans pipeline.dim_supplier. C'est voulu : cela
-- permet de mesurer la propagation des erreurs d'extraction sur les requêtes BI.
CREATE TABLE IF NOT EXISTS gold.dim_supplier (
    supplier_id   SERIAL      PRIMARY KEY,
    supplier_name TEXT        NOT NULL,
    CONSTRAINT uq_gold_supplier_name UNIQUE (supplier_name)
);

-- Dimension date (pré-calculée à l'insertion)
CREATE TABLE IF NOT EXISTS gold.dim_date (
    date_id    INTEGER     PRIMARY KEY,   -- format YYYYMMDD
    full_date  DATE        NOT NULL,
    year       SMALLINT    NOT NULL,
    quarter    SMALLINT    NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    month      SMALLINT    NOT NULL CHECK (month BETWEEN 1 AND 12),
    month_name TEXT        NOT NULL,
    CONSTRAINT uq_gold_full_date UNIQUE (full_date)
);

-- Dimension taux de TVA
CREATE TABLE IF NOT EXISTS gold.dim_vat (
    vat_id   SERIAL         PRIMARY KEY,
    vat_rate NUMERIC(5, 4)  NOT NULL,
    CONSTRAINT uq_gold_vat_rate UNIQUE (vat_rate)
);

-- Table de faits — factures cohérentes uniquement (is_inconsistent=False)
CREATE TABLE IF NOT EXISTS gold.fact_invoice (
    invoice_key    SERIAL          PRIMARY KEY,
    invoice_id     TEXT            NOT NULL,
    invoice_number TEXT            NOT NULL,
    supplier_id    INTEGER         NOT NULL REFERENCES gold.dim_supplier(supplier_id),
    date_id        INTEGER         NOT NULL REFERENCES gold.dim_date(date_id),
    vat_id         INTEGER         NOT NULL REFERENCES gold.dim_vat(vat_id),
    net_amount     NUMERIC(14, 3)  NOT NULL,
    vat_amount     NUMERIC(14, 3)  NOT NULL,
    total_amount   NUMERIC(14, 3)  NOT NULL,
    is_valid       BOOLEAN         NOT NULL DEFAULT TRUE,
    source_file    TEXT,
    run_name       TEXT,
    loaded_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_gold_fact_supplier ON gold.fact_invoice(supplier_id);
CREATE INDEX IF NOT EXISTS ix_gold_fact_date     ON gold.fact_invoice(date_id);
CREATE INDEX IF NOT EXISTS ix_gold_fact_vat      ON gold.fact_invoice(vat_id);

-- Factures incohérentes (is_inconsistent=True) — même structure que pipeline.rejected_invoice
-- errors contient le type d'incohérence (wrong_total | wrong_vat)
CREATE TABLE IF NOT EXISTS gold.rejected_invoice (
    id          SERIAL      PRIMARY KEY,
    invoice_id  TEXT        NOT NULL,
    run_name    TEXT        NOT NULL,
    errors      TEXT        NOT NULL,   -- inconsistency_type (wrong_total | wrong_vat)
    raw_json    TEXT,
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_gold_rejected_run ON gold.rejected_invoice(run_name);


-- ===========================================================================
-- SCHÉMA pipeline
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS pipeline AUTHORIZATION invoice_loader;

-- Note : aucune normalisation des noms de fournisseurs dans pipeline.dim_supplier.
-- Un nom extrait différent (ex. "Haddad Pro SUARL, Nabeul" au lieu de "Haddad Pro SUARL")
-- crée une nouvelle ligne. C'est voulu : permet de mesurer la propagation des erreurs
-- d'extraction (supplier_extra_text, etc.) sur les requêtes BI et les agrégats.
CREATE TABLE IF NOT EXISTS pipeline.dim_supplier (
    supplier_id   SERIAL  PRIMARY KEY,
    supplier_name TEXT    NOT NULL,
    CONSTRAINT uq_pipeline_supplier_name UNIQUE (supplier_name)
);

CREATE TABLE IF NOT EXISTS pipeline.dim_date (
    date_id    INTEGER     PRIMARY KEY,
    full_date  DATE        NOT NULL,
    year       SMALLINT    NOT NULL,
    quarter    SMALLINT    NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    month      SMALLINT    NOT NULL CHECK (month BETWEEN 1 AND 12),
    month_name TEXT        NOT NULL,
    CONSTRAINT uq_pipeline_full_date UNIQUE (full_date)
);

CREATE TABLE IF NOT EXISTS pipeline.dim_vat (
    vat_id   SERIAL        PRIMARY KEY,
    vat_rate NUMERIC(5, 4) NOT NULL,
    CONSTRAINT uq_pipeline_vat_rate UNIQUE (vat_rate)
);

-- Fiches valides (is_valid=True)
CREATE TABLE IF NOT EXISTS pipeline.fact_invoice (
    invoice_key    SERIAL          PRIMARY KEY,
    invoice_id     TEXT            NOT NULL,
    invoice_number TEXT,
    supplier_id    INTEGER         NOT NULL REFERENCES pipeline.dim_supplier(supplier_id),
    date_id        INTEGER         NOT NULL REFERENCES pipeline.dim_date(date_id),
    vat_id         INTEGER         NOT NULL REFERENCES pipeline.dim_vat(vat_id),
    net_amount     NUMERIC(14, 3)  NOT NULL,
    vat_amount     NUMERIC(14, 3)  NOT NULL,
    total_amount   NUMERIC(14, 3)  NOT NULL,
    is_valid       BOOLEAN         NOT NULL DEFAULT TRUE,
    source_file    TEXT,
    run_name       TEXT,
    loaded_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_pipeline_fact_supplier ON pipeline.fact_invoice(supplier_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_fact_date     ON pipeline.fact_invoice(date_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_fact_vat      ON pipeline.fact_invoice(vat_id);

-- Fiches rejetées (is_valid=False)
CREATE TABLE IF NOT EXISTS pipeline.rejected_invoice (
    id          SERIAL      PRIMARY KEY,
    invoice_id  TEXT        NOT NULL,
    run_name    TEXT        NOT NULL,
    errors      TEXT        NOT NULL,
    raw_json    TEXT,
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_pipeline_rejected_run ON pipeline.rejected_invoice(run_name);

-- Traçabilité des chargements pipeline
CREATE TABLE IF NOT EXISTS pipeline.load_info (
    id                    SERIAL       PRIMARY KEY,
    run_name              TEXT         NOT NULL,
    dataset               TEXT         NOT NULL,
    ground_truth_sha256   TEXT,
    n_loaded              INTEGER      NOT NULL,
    n_rejected            INTEGER      NOT NULL,
    loaded_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);


-- ===========================================================================
-- SCHÉMA history
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS history AUTHORIZATION invoice_loader;

CREATE TABLE IF NOT EXISTS history.invoice_history (
    id             SERIAL          PRIMARY KEY,
    invoice_number TEXT            NOT NULL,
    supplier_name  TEXT            NOT NULL,
    invoice_date   DATE            NOT NULL,
    net_amount     NUMERIC(14, 3)  NOT NULL,
    vat_amount     NUMERIC(14, 3)  NOT NULL,
    total_amount   NUMERIC(14, 3)  NOT NULL,
    vat_rate       NUMERIC(5, 4)   NOT NULL,
    n_lines        SMALLINT        NOT NULL,
    anomaly_type   TEXT            NOT NULL DEFAULT '',
    loaded_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_history_supplier ON history.invoice_history(supplier_name);
CREATE INDEX IF NOT EXISTS ix_history_date     ON history.invoice_history(invoice_date);


-- ===========================================================================
-- PERMISSIONS bi_readonly
-- ===========================================================================

GRANT USAGE ON SCHEMA gold     TO bi_readonly;
GRANT USAGE ON SCHEMA pipeline TO bi_readonly;

GRANT SELECT ON ALL TABLES IN SCHEMA gold     TO bi_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline TO bi_readonly;

ALTER DEFAULT PRIVILEGES FOR ROLE invoice_loader IN SCHEMA gold
    GRANT SELECT ON TABLES TO bi_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE invoice_loader IN SCHEMA pipeline
    GRANT SELECT ON TABLES TO bi_readonly;
