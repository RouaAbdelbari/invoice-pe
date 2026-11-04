# Dossier prompts/fewshot/

Contient les fichiers d'exemples few-shot injectés via `extract.py --fewshot <nom>`.

## Format d'un fichier few-shot

```json
{
  "description": "Description courte des exemples choisis et de leur objectif",
  "examples": [
    {
      "invoice_id": "dev_001",
      "expected": {
        "supplier_name": "Nom exact tel qu'imprimé",
        "invoice_date": "2025-07-26",
        "net_amount": "19262.090",
        "vat_amount": "1348.350",
        "total_amount": "20610.440"
      }
    }
  ]
}
```

## Règles

- Les `invoice_id` doivent référencer des factures de `data/dev/` uniquement.
- Les valeurs `expected` sont construites à partir de `data/dev/ground_truth.csv`.
- Ces factures sont **exclues du calcul des métriques dev** dans `evaluate.py`
  quand elles servent d'exemples (signalé dans le résumé).
- Les fichiers few-shot sont rédigés ou validés par l'étudiant.
  `extract.py` ne les utilise jamais sans que l'étudiant les ait placés ici.
- Le prompt cible doit contenir le marqueur `{fewshot}` pour que l'injection fonctionne.
