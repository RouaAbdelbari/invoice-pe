# Brief pour Codex — Terminer le projet « Prompt Engineering : extraction de factures + Text-to-SQL »

Tu reprends un projet académique individuel (5e année ingénieur BI, module Prompt Engineering) déjà bien
avancé par un autre assistant. **Le cœur du projet est une étude expérimentale comparant des stratégies
de prompting**, sur un pipeline en trois étapes : extraction de factures par LLM → PostgreSQL →
questions en langage naturel (Text-to-SQL), plus un module de détection d'anomalies et une restitution BI.

Ton rôle : développeur assistant. L'étudiant décide du contenu final des prompts, valide chaque étape et
lance lui-même le jeu de test. Travaille **bloc par bloc** (section 5) et **arrête-toi à chaque point de
contrôle** en montrant ce que tu as fait.

Le rendu a lieu dans environ 30 heures. Les tests finaux tourneront **cette nuit** : le script
`run_final.ps1` (bloc 4) doit être prêt avant 23 h.

---

## 1. Environnement

- Windows 11, PowerShell, VS Code. Python 3.12 dans `.venv` → toujours utiliser `.venv\Scripts\python.exe`.
- Pour éviter les problèmes d'accents dans la console : `$env:PYTHONIOENCODING="utf-8"` avant chaque commande.
- LLM local : Ollama, modèle `qwen2.5:7b`, sur une GTX 1650 de 4 Go (exécution mixte CPU/GPU,
  environ 10 à 15 s par facture, 15 à 60 s par question Text-to-SQL).
- **Un seul processus qui appelle le LLM à la fois.** Ne lance jamais deux runs en parallèle.
- PostgreSQL 16 (une ancienne version 9.3 existe aussi : ne pas l'utiliser), base `invoice_pe`.
  Identifiants dans `.env`, lus uniquement par `src/db.py`.
- Lance `pytest` après chaque modification : tous les tests doivent passer (actuellement plus de 150).

## 2. État actuel (lis ces fichiers avant toute modification)

```
invoice-pe/
├── KIRO_BRIEF.md            brief initial complet : spécifications détaillées des phases A à E — À LIRE
├── data/
│   ├── dev/      20 factures (conception des prompts)
│   ├── dev2/     40 factures (validation, jamais vues lors de l'écriture du v1)
│   ├── test/     100 factures — PROTÉGÉ, jamais utilisé avant les tests finaux
│   ├── history/  500 factures structurées (entraînement Isolation Forest)
│   └── questions/ questions_dev.json (10), questions_test.json (25), questions_security.json (8)
├── prompts/
│   ├── extraction_v0.txt, extraction_v1.txt     figés
│   ├── sql_v0.txt, sql_v1.txt, answer_v0.txt    figés
│   ├── fewshot/  drafts/  CHANGELOG.md
├── runs/                    un dossier par exécution (run_info.json, results.csv, raw/, eval_*)
├── sql/                     init.sql, schema.sql (étoile : fact_invoice, dim_supplier, dim_date, dim_vat ; schémas gold, pipeline, history)
├── src/
│   ├── generator.py         données synthétiques (graine 42 ; option --extra-dev pour dev2)
│   ├── llm.py               SEUL point d'appel au LLM (temp 0, seed 42, num_ctx 4096, num_predict 512, délai 180 s)
│   ├── validate.py          analyse JSON, normalisation, contrôles arithmétiques
│   ├── extract.py           étape 1 (--set, --prompt, --repeat, --fewshot, --confirm-test)
│   ├── evaluate.py          évaluation extraction (McNemar, IC bootstrap, catégories d'erreurs, --compare)
│   ├── db.py, load_db.py    connexions et chargement gold / pipeline (+ pipeline.load_info)
│   ├── sql_guard.py         contrôle SQL (SELECT seul, tables non qualifiées, liste blanche, LIMIT)
│   ├── text2sql.py          étape 2 (SET search_path gold|pipeline, temps par étape)
│   └── evaluate_sql.py      N1/N2, exécution, n3_sql et n3_truth (strict/souple), nombres fidèles/vrais, --propagation
├── tools/compare_sql_final.py
└── tests/
```

**Résultats obtenus jusqu'ici (contexte uniquement, ne les recopie dans aucun document) :**
- Extraction : v1 (structured output) contre v0 (zero-shot). Sur dev2, 97,5 % contre 67,5 % de factures
  100 % correctes, McNemar p = 0,0018. Seule erreur restante du v1 : `dev2_040`, où le TTC (« Net à payer »)
  est placé dans `net_amount` (confusion lexicale probable).
- Text-to-SQL : sql_v1 (schema-aware) contre sql_v0. Sur dev, exécution 100 % contre 50 %, n3_sql souple
  80 % contre 40 %. Erreurs restantes du v1 : `dev_q08` (colonne `month_name` absente du SELECT) et
  `dev_q10` (colonne `invoice_id` absente).
- Propagation : n3_truth souple 80 % sur gold contre 30 % sur pipeline (pipeline chargé depuis
  l'extraction v0) ; 5/10 questions changent, toutes prévues comme sensibles.
- Sécurité : 8/8 questions malveillantes bloquées.

## 3. Règles méthodologiques NON NÉGOCIABLES

1. **Jeu de test protégé.** N'exécute jamais `--set test` ni les questions de test. Ne lis jamais
   `data/test/` ni `questions_test.json` pour concevoir un prompt ou des exemples. Les exemples few-shot
   viennent uniquement de `data/dev/` et `questions_dev.json`.
2. **Paramètres du modèle constants**, uniquement via `src/llm.py`. Aucun autre appel à `ollama`.
3. **Prompts immuables et versionnés.** Une amélioration = un nouveau fichier, avec une entrée dans
   `prompts/CHANGELOG.md` (date, parent, modification unique, erreur observée qui la justifie, run de référence).
4. **Une seule modification par rapport au prompt parent.**
5. **Montre chaque nouveau texte de prompt à l'étudiant avant de l'exécuter.**
6. **Code déterministe identique pour toutes les versions.** Si tu modifies une métrique ou la validation,
   réévalue toutes les exécutions existantes (sans relancer le LLM) et signale-le dans le README.
7. **Aucun résultat inventé**, aucune conclusion rédigée. Les documents décrivent comment reproduire.
8. **Sécurité SQL** : le LLM ne voit jamais d'identifiants ; toute requête générée passe par `sql_guard`
   et s'exécute sous le rôle `bi_readonly`.
9. **Ne supprime jamais** un run ni une sortie brute existante.

## 4. Tâches restantes

### Bloc 1 — Finitions de l'évaluation et variantes de prompts (avec le LLM)

1.1 **Petit correctif d'affichage** dans `tools/compare_sql_final.py` : les métriques « nombres fidèles »
    et « nombres vrais » doivent afficher le bon dénominateur (questions ayant une réponse : 5 pour v0,
    10 pour v1), et non /10 partout.

1.2 **Extraction v2a et v2b** (deux variantes concurrentes du v1, une modification chacune) :
   - `prompts/extraction_v2a.txt` = copie exacte de `extraction_v1.txt` + une phrase après la définition
     des champs : `Attention : « Net à payer » et « Amount due » désignent le montant TTC (total_amount), jamais le montant HT.`
   - `prompts/extraction_v2b.txt` = copie exacte de `extraction_v1.txt` + l'emplacement `{fewshot}` juste
     avant `Facture :`, et `prompts/fewshot/fs_v2b.json` avec **2 exemples de `data/dev/` uniquement** :
     une facture `compact` (« Net à payer ») et une `modern_en` (« Amount due »), chacune avec le JSON
     attendu construit à partir de `data/dev/ground_truth.csv`. **Montre les exemples avant de lancer.**
   - Runs (un à la fois) : v2a sur `dev` puis `dev2` ; v2b sur `dev` (sans les 2 exemples) puis `dev2`.
   - Compare chaque variante au v1 (`evaluate.py --compare`) sur dev et dev2. Tableau récapitulatif :
     factures 100 % correctes, catégories d'erreurs, détections justifiées, faux rejets, **nouvelles
     erreurs apparues par rapport au v1**.

1.3 **sql_v2** (une seule modification du `sql_v1`, ciblant les colonnes manquantes de dev_q08 et dev_q10).
    Proposition à montrer à l'étudiant, ajoutée avant `Question :` :
    `La requête doit renvoyer toutes les informations demandées par la question (par exemple le nom du mois ou l'identifiant de la facture, en plus des montants).`
    Runs sur les questions dev, gold puis pipeline. Comparaison v1/v2 (n3_sql et n3_truth, strict/souple, McNemar).

1.4 **answer_v1** (une seule modification du `answer_v0`, ciblant les nombres non fidèles, par ex. dev_q07).
    Analyse d'abord les réponses KO en « nombres fidèles » du run sql_v1 gold : arrondi, recalcul ou
    invention ? Propose ensuite une phrase ciblée, par exemple :
    `Cite les nombres exactement comme dans les résultats, sans les arrondir ni les recalculer.`
    Montre l'analyse et la proposition, puis lance (avec sql_v1, questions dev, gold) et compare à answer_v0.

**Point de contrôle 1** : tableaux comparatifs de 1.2, 1.3 et 1.4. L'étudiant choisit alors la meilleure
version d'extraction, de SQL et de réponse pour les tests finaux.

### Bloc 2 — Phase D : détection d'anomalies (sur dev et dev2 uniquement)

Voir `KIRO_BRIEF.md`, phase D. En résumé :
- `src/anomalies.py` : features par facture (TTC, log TTC, z-score du montant par fournisseur calculé sur
  `history` uniquement, écart du taux de TVA au taux habituel du fournisseur, nombre de lignes si disponible,
  doublon probable : même fournisseur, montant identique à ±0,5 % dans les 7 jours précédents, en
  cherchant dans l'historique et dans le jeu évalué).
- Trois détecteurs comparés sur les mêmes factures : règles Python (seuils documentés), Isolation Forest
  (entraîné sur `history`, graine fixe, modèle sauvegardé dans `models/`), et un prompt LLM
  `prompts/anomaly_v0.txt` (la facture + un court résumé de l'historique du fournisseur ; réponse JSON
  `{"is_anomaly": bool, "type": ..., "reason": ...}`), à **montrer avant exécution**.
- Métriques : précision, rappel, F1, globales et par type (`amount_outlier`, `unusual_vat`, `duplicate`).
- Évaluation sur gold et sur pipeline (effet des erreurs d'extraction sur les alertes).
- Tests unitaires pour le calcul des features.

**Point de contrôle 2** : résultats sur dev et dev2.

### Bloc 3 — Restitution BI et démonstration (sans le LLM, sauf un test court de l'application)

3.1 Vues SQL (dans gold et pipeline) : `v_kpi_monthly` (HT, TVA et TTC par mois et trimestre),
    `v_supplier_spend`, `v_alerts` (anomalies détectées), `v_kpi_confidence` (part des montants issus de
    fiches validées contre rejetées).
3.2 `src/export_bi.py` → dossier `bi/` : CSV avec séparateur `;` et encodage UTF-8 avec BOM :
    métriques d'extraction par version de prompt et par jeu, par mise en page et par format de nombres,
    catégories d'erreurs ; métriques SQL par version (n3_sql et n3_truth) ; propagation question par
    question avec la sensibilité prévue ; anomalies par détecteur et par type ; KPI métier depuis les vues.
3.3 `src/app.py` (Streamlit) : zone de question, choix gold/pipeline et de la version de prompt SQL,
    affichage de la réponse, du SQL généré, du tableau de résultats, du statut du contrôle SQL et des
    alertes de vérification des nombres. Ajouter `streamlit` à `requirements.txt` (version figée).
    Commande : `.venv\Scripts\python.exe -m streamlit run src\app.py`.
3.4 Mettre à jour `README.md` : installation, ordre d'exécution complet, description de chaque script,
    règles méthodologiques, reproduction des résultats, lancement de l'application.

**Point de contrôle 3** : captures ou description de l'application, liste des CSV exportés.

### Bloc 4 — Script des tests finaux `run_final.ps1` (à préparer avant 23 h, NE PAS LANCER)

Paramètres en tête de script, renseignés par l'étudiant après le point de contrôle 1 :
`$BEST_EXTRACTION`, `$BEST_SQL`, `$BEST_ANSWER`.

Enchaînement, **un run à la fois**, avec journal horodaté dans `runs/final_log.txt`. Une étape en échec est
journalisée et le script continue :
1. Une seule confirmation au démarrage (« OUI »), transmise ensuite à chaque appel via `--confirm-test`.
2. Extraction sur `test` : v0, v1, v2a, v2b.
3. Chargement : gold (test) ; pipeline depuis `$BEST_EXTRACTION` (test) ; un second schéma ou chargement
   séparé depuis v0 (test) pour le contraste de propagation. Si un second schéma est nécessaire
   (`pipeline_v0`), crée-le avec la même structure, les mêmes droits `bi_readonly` et les mêmes tests.
4. Text-to-SQL sur `questions_test.json` : sql v0, v1, v2 (avec `$BEST_ANSWER`) sur gold ; puis
   `$BEST_SQL` sur pipeline (meilleure extraction) et sur pipeline_v0.
5. Questions de sécurité avec `$BEST_SQL`.
6. Anomalies : les trois détecteurs sur gold et sur pipeline (test).
7. Toutes les évaluations et comparaisons (McNemar entre versions successives), propagation, `export_bi.py`.
8. Au démarrage, afficher une estimation de la durée totale (environ 12 s par facture, 60 s par question) ;
   à la fin, un résumé des étapes réussies et échouées.

Ajoute `run_final_dryrun.ps1` qui exécute exactement le même enchaînement sur **dev** avec `--limit 2`
et 2 questions, pour vérifier que tout s'enchaîne sans erreur. Lance-le toi-même et montre la sortie.

**Point de contrôle 4** : sortie du dry-run et estimation de la durée du vrai run.

## 5. Ordre de travail et définition de « terminé »

Ordre : bloc 1 → bloc 2 → bloc 3 → bloc 4. Si le temps manque, prépare le bloc 4 (`run_final.ps1` et le
dry-run) **avant** de finir le bloc 3 : les tests finaux doivent tourner cette nuit.

Un bloc est terminé quand `pytest` passe, que les scripts tournent sur dev ou dev2 depuis PowerShell, que
le README est à jour, et que tu as listé les fichiers créés ou modifiés et ce que l'étudiant doit vérifier.

Ce que tu ne fais pas : lancer le jeu de test ou les questions de test, modifier un prompt figé ou
`llm.py`, écrire des conclusions ou des chiffres dans un document, ajouter des fonctionnalités hors de ce
brief (agents, RAG, fine-tuning).

Commence par lire `KIRO_BRIEF.md`, `prompts/CHANGELOG.md`, `src/llm.py`, `src/extract.py`,
`src/evaluate.py`, `src/text2sql.py` et `src/evaluate_sql.py`, puis présente ton plan pour le bloc 1.
