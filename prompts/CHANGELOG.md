# CHANGELOG des prompts d'extraction

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

## extraction_v1.txt (à venir)

- **Parent** : extraction_v0
- **Stratégie** : structured output prompting — définition précise de chaque champ,
  format de sortie imposé (JSON seul), règles de normalisation des dates et des montants
- **Hypothèses testées** : disparition des balises, de `supplier_extra_text` et de `amount_x1000`
- **Exécution de référence** : à compléter
- **Résultats dev** : à compléter