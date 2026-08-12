from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import spacy

from analyze_tallyqa_30000 import CLASS_ALIASES


ROOT = Path(__file__).resolve().parents[1]
SIMPLE_TEMPLATES = (
    "are in the picture",
    "are in the photo",
    "can be seen",
    "can you see",
    "are there",
    "are visible",
    "do you see",
)
OBJECT_NAMES = sorted(
    {alias for aliases in CLASS_ALIASES.values() for alias in aliases},
    key=len,
    reverse=True,
)
COMPLEX_DEPENDENCIES = {"amod", "aux", "acomp", "pobj", "prep", "dobj", "compound", "ccomp"}


def classifier_text(question: str) -> str:
    text = question.strip().lower()
    return text[9:] if text.startswith("how many ") else text


def matches_simple_template(text: str) -> bool:
    normalized = text.strip(" ?.,!")
    return any(
        normalized == f"{object_name} {template}"
        for object_name in OBJECT_NAMES
        for template in SIMPLE_TEMPLATES
    )


def is_simple(doc) -> bool:
    if matches_simple_template(doc.text):
        return True
    return not any(token.dep_ in COMPLEX_DEPENDENCIES for token in doc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1024)
    args = parser.parse_args()

    source = ROOT / "data" / "metadata" / "train.json"
    rows = json.loads(source.read_text(encoding="utf-8"))
    nlp = spacy.load("en_core_web_sm", disable=["ner", "lemmatizer"])
    texts = (classifier_text(row.get("question", "")) for row in rows)
    counts = Counter()

    for row, doc in zip(rows, nlp.pipe(texts, batch_size=args.batch_size)):
        row["issimple"] = is_simple(doc)
        counts["simple" if row["issimple"] else "complex"] += 1

    summary = {
        "total": len(rows),
        "simple": counts["simple"],
        "complex": counts["complex"],
        "paper_expected_simple": 188439,
        "paper_expected_complex": 60879,
        "matches_paper_counts": counts["simple"] == 188439 and counts["complex"] == 60879,
    }
    if args.write:
        output = ROOT / "data" / "metadata" / "train_classified.json"
        output.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        (ROOT / "data" / "metadata" / "train_classified_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        summary["output"] = str(output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
