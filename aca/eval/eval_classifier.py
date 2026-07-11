"""
Évaluation de la précision du classifieur (P1 §11.4 item 11) : fait passer le jeu d'e-mails
étiquetés (`eval_dataset.json`, 50 exemples synthétiques couvrant les 5 catégories, dont quelques
cas volontairement ambigus) dans `classifier_node`, compare à la catégorie attendue, et rapporte la
précision globale, par catégorie, et la liste des erreurs pour analyse.

Lancement : `python eval_classifier.py`
"""
import json
import os

from aca.core import app

DATASET_PATH = os.path.join(os.path.dirname(__file__), "eval_dataset.json")


def run() -> None:
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        examples = json.load(f)

    correct = 0
    errors = []
    per_category = {}  # catégorie -> [total, corrects]

    for ex in examples:
        state = {"email_raw": {"sender": ex["sender"], "subject": ex["subject"], "body": ex["body"]}}
        predicted = app.classifier_node(state)["classification"]
        expected = ex["expected_category"]

        totals = per_category.setdefault(expected, [0, 0])
        totals[0] += 1
        if predicted == expected:
            correct += 1
            totals[1] += 1
        else:
            errors.append({"subject": ex["subject"], "expected": expected, "predicted": predicted})

    total = len(examples)
    print(f"\n=== Précision globale : {correct}/{total} ({100 * correct / total:.1f}%) ===\n")
    print("Par catégorie :")
    for cat in sorted(per_category):
        tot, corr = per_category[cat]
        print(f"  {cat:15s} {corr}/{tot} ({100 * corr / tot:.0f}%)")

    if errors:
        print(f"\n{len(errors)} erreur(s) :")
        for e in errors:
            print(f"  - « {e['subject']} » — attendu {e['expected']}, prédit {e['predicted']}")
    else:
        print("\nAucune erreur.")


if __name__ == "__main__":
    run()
