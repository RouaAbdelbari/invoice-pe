# Observations — extraction_v0 (jeu dev, qwen2.5:7b)

Exécution : runs/20261103_113216_extraction_v0_dev (--limit 3)
Résumé : JSON strict 0/3 · JSON tolérant 3/3 · valides 1/3

## Observations par facture
| Facture | Layout | Format de la réponse | Erreurs constatées |
|---|---|---|---|
| dev_001 | classic_fr | JSON entouré de ``` | aucune |
| dev_002 | modern_en | JSON entouré de ``` | aucune — rejet correct (TVA fausse injectée) |
| dev_003 | compact (format anglais) | JSON entouré de ``` | montants ×1000 : le LLM a renvoyé 9899394 au lieu de 9899.394 — erreur du LLM, pas de la normalisation |

## Catégories d'erreurs
- Format : JSON entouré de balises ``` (3/3)
- Montants : séparateurs mal interprétés sur le format anglais 9,899.394 (dev_003)
- Dates : laissées au format d'origine, converties par Python

## Idées pour extraction_v1
- Exiger un JSON seul, sans balises ni texte
- Imposer les montants en nombres JSON avec un point décimal et 3 décimales (ex. 9899.394)
- Rappeler qu'une facture peut utiliser le format français (1 234,567) ou anglais (1,234.567)
- Imposer les dates au format AAAA-MM-JJ
Exécution complète : runs/20261104_122418_extraction_v0_dev
Résumé : JSON strict 0 % · tolérant 100 % · factures 100 % correctes 8/10 · détections justifiées 1/2

| dev_008 | table_mixed | JSON entouré de ``` | fournisseur avec la ville : « Ferchichi Solutions SARL, Nabeul » |

Catégorie ajoutée :
- Fournisseur : le LLM recopie la ligne entière (nom + ville) au lieu du seul nom (dev_008)