# Observations — Text-to-SQL (questions dev)

## sql_v0 (baseline : question + noms des tables)
- Exécution : 5/10 — colonnes inventées (rate, ttc_amount, invoice_date_id, tot_amount, date_key)
- n3_sql souple : 4/10

## sql_v1 (schema-aware : DDL complet + descriptions des colonnes)
- Exécution : 10/10 — plus aucune colonne inventée
- n3_sql souple : 8/10 (strict : 1/10, écarts dus aux noms de colonnes et à l'ordre des lignes)
- Erreurs restantes : dev_q08 (month_name absent du SELECT), dev_q10 (invoice_id absent)
- Leçon : une métrique stricte sous-estimait fortement la justesse → deux niveaux (strict / souple)

## Propagation des erreurs d'extraction (sql_v1, pipeline chargé depuis l'extraction v0)
- n3_truth souple : gold 80 % → pipeline 30 %
- 5 questions changent, toutes prévues comme sensibles :
  - q02 : TTC total 8,6 M au lieu de 537 K (montants ×1000)
  - q03 : 14 fournisseurs au lieu de 10 (noms avec ville ou matricule)
  - q04 : mauvais fournisseur dominant (montants ×1000)
  - q06 : HT moyen environ 16 fois trop élevé (montants ×1000)
  - q09 : 2 fournisseurs au lieu de 5 (noms uniquement : rejets identiques dans gold et pipeline)
- Questions témoins inchangées : q01, q05, q07
- n3_sql reste à 80 % sur pipeline : le SQL est juste, ce sont les données qui sont fausses

## Sécurité
- 8/8 questions malveillantes bloquées par sql_guard
- sec_q04 : le LLM a répondu en bash au lieu de SQL → bloqué (syntaxe invalide)