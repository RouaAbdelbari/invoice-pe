# CHANGELOG des prompts d'extraction et Text-to-SQL

Chaque entrée correspond à un fichier de prompt immuable.
Une amélioration = un nouveau fichier. Un fichier testé n'est jamais modifié.
Règle : chaque version ne change qu'une seule chose par rapport à son parent —
soit une stratégie de prompting complète (ex. structured output), soit, pour les
versions d'ablation, un seul facteur. Chaque modification doit répondre à une
erreur observée sur le jeu dev.

---

## extraction_v0.txt

- **Date** : 2026-11-03
- **Parent** : aucun (baseline)
- **Stratégie** : zero-shot minimal (liste des 5 champs + demande de JSON, sans règles de format)
- **Exécution de référence** : runs/<dossier du run v0 sur 20 factures dev>
- **Résultats dev** : JSON strict 0 % · factures 100 % correctes 60 % (IC95 % [40 %–80 %])
- **Erreurs observées** :
  - Format : JSON systématiquement entouré de balises ``` (20/20)
  - Fournisseur (`supplier_extra_text`, 5 factures) : sur les gabarits `table_mixed`
    et `modern_en`, où le nom et l'adresse sont sur la même ligne, le nom est recopié
    avec la ville ou le matricule fiscal
    (ex. « Haddad Pro SUARL, Nabeul », « Ben Salah … (Nabeul, Tunisia) - Tax ID … »)
  - Montants ×1000 (`amount_x1000`, 2 factures : dev_002 en format fr, dev_019 en
    format en) : le LLM renvoie des entiers (2988529 au lieu de 2988.529).
    Hypothèse : les 3 décimales du dinar (millimes) ressemblent à un groupe de
    milliers, quel que soit le format
  - Dates : laissées au format d'origine (ex. « 16/09/2025 ») ; la conversion ISO
    est faite par validate.py, pas par le LLM
  - Arrondi (`amount_other`, dev_001) : le LLM arrondit à 2 décimales
    (1348.35 au lieu de 1348.346) — même cause probable : les 3 décimales du dinar

---

## extraction_v1.txt

- **Date** : 2026-11-04
- **Parent** : extraction_v0
- **Modification** : structured output prompting — ajout de règles de format explicites
  (JSON seul sans balises, nom fournisseur seul sans ville ni matricule,
  date ISO, montants à 3 décimales avec point décimal, rappel millimes ≠ milliers).
  Note : cette version corrige plusieurs symptômes simultanément sous une même stratégie ;
  les versions d'ablation v2a/v2b isoleront les facteurs si nécessaire.
- **Erreurs observées justifiant cette version** :
  - JSON entouré de balises ``` (20/20 sur v0) → règle 1
  - `supplier_extra_text` (5/20) : ville ou matricule inclus dans le nom → règle 2
  - `amount_x1000` (2/20 : dev_002, dev_019) : millimes confondus avec milliers → règle 4
  - `amount_other` arrondi à 2 décimales (dev_001) → règle 4
- **Hypothèses testées** : disparition des balises, réduction de supplier_extra_text,
  réduction de amount_x1000 et amount_other
- **Exécution de référence** : runs/20261104_204605_extraction_v1_dev (20 factures dev)
- **Résultats dev** : JSON strict 100 % · factures 100 % correctes 100 % (IC95 % [100 %–100 %])
  · supplier_name 100 % · tous les champs 100 % · latence moy. 10.86 s
  · McNemar vs v0 : p=0.0078 *** (8 discordants, toutes en faveur de v1)

---

## Prompts Text-to-SQL

### sql_v0.txt

- **Date** : 2026-11-04
- **Parent** : aucun (baseline SQL)
- **Stratégie** : zero-shot minimal — question + liste des noms de tables sans schéma ni colonnes.
  Le search_path est positionné par text2sql.py avant chaque exécution.
  Les tables ne sont pas qualifiées (schéma absent) : sql_guard refuse toute table qualifiée.
- **Marqueur** : `{tables}` (liste des noms de tables, pas `{schema}`)
- **Hypothèses testées** : le LLM peut-il générer du SQL correct avec uniquement les noms de tables ?
- **Exécution de référence** : runs/20261105_122150_sql_v0_sql_dev_gold (gold, 10 questions dev)
                               runs/20261105_134045_sql_v0_sql_dev_pipeline (pipeline, 10 questions dev)
- **Résultats dev gold** : SQL strict 0% · lenient 100% · garde 100% · N3 sémantique 10%
  · exec OK 5/10 · erreurs : colonnes hallusinées (rate, ttc_amount, invoice_date_id, tot_amount)
- **Résultats dev pipeline** : N3 sémantique 0% (1 question de plus échoue vs gold : dev_q03 fournisseurs)
- **Propagation** : 1 question change (dev_q03 supplier_name) — gold=OK, pipeline=KO
  (noms fragmentés dans pipeline créent plus de lignes distinctes)

### answer_v0.txt

- **Date** : 2026-11-04
- **Parent** : aucun (baseline réponse)
- **Stratégie** : zéro-shot minimal — question + résultats bruts JSON, réponse en français.
- **Marqueurs** : `{question}`, `{rows}`
- **Exécution de référence** : à compléter
- **Résultats dev** : à compléter

### sql_v1.txt

- **Date** : 2026-11-05
- **Parent** : sql_v0
- **Modification** : remplacement du marqueur `{tables}` (liste nue) par `{schema}`
  (DDL complet avec types, descriptions, exemples de valeurs, clés de jointure).
  C'est la seule modification par rapport à sql_v0 — stratégie schema-aware.
- **Justification** : sql_v0 génère des colonnes inexistantes car le LLM ne connaît
  pas le DDL réel. Colonnes inventées observées en v0 :
  `rate` (au lieu de `vat_rate`), `ttc_amount` (au lieu de `total_amount`),
  `invoice_date_id` (au lieu de `date_id`), `tot_amount`, `date_key`, `date`
- **Hypothèse testée** : le DDL complet élimine les hallucinations de colonnes
  et améliore N3 (correction sémantique)
- **Exécution de référence** : runs/20261105_140848_sql_v1_sql_dev_gold (gold, 10 questions dev)
                               runs/20261105_141530_sql_v1_sql_dev_pipeline (pipeline, 10 questions dev)
- **Résultats dev gold** : exec OK 100% (vs 50% en v0) · N3 sémantique 10% (vs 10% en v0)
  · McNemar exec_ok : p=0.0625 (5 discordants, tous v0=KO vers v1=OK)
  · McNemar N3 : p=1.0 (1 discordant, non significatif)
- **Résultats dev pipeline** : exec OK 100% (vs 50%) · N3 sémantique 10% (vs 0%)
- **Diagnostic** : le DDL complet elimine les hallucinations de colonnes (5/5 exec
  reparees) mais le LLM produit encore des requetes semantiquement incorrectes
  (mauvaises jointures, mauvais filtres, calculs errones). Le gain principal est sur
  exec_ok, pas encore sur N3.

### drafts/answer_v0_draft.txt (non utilisé)
- Reserve comme point de depart pour answer_v1
