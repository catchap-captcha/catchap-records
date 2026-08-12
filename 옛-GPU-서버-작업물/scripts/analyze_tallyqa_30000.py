from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PREFIXES = ("VG_100K/", "VG_100K_2/", "train2014/", "val2014/")
CLASS_ALIASES = {
    "person": {"person", "people", "man", "men", "woman", "women", "boy", "boys", "girl", "girls", "child", "children", "kid", "kids", "player", "players"},
    "bicycle": {"bicycle", "bicycles", "bike", "bikes"},
    "car": {"car", "cars", "vehicle", "vehicles"},
    "motorcycle": {"motorcycle", "motorcycles", "motorbike", "motorbikes"},
    "airplane": {"airplane", "airplanes", "plane", "planes"},
    "bus": {"bus", "buses"},
    "train": {"train", "trains"},
    "truck": {"truck", "trucks"},
    "boat": {"boat", "boats"},
    "bird": {"bird", "birds"},
    "cat": {"cat", "cats"},
    "dog": {"dog", "dogs"},
    "horse": {"horse", "horses"},
    "sheep": {"sheep"},
    "cow": {"cow", "cows", "cattle"},
    "elephant": {"elephant", "elephants"},
    "bear": {"bear", "bears"},
    "zebra": {"zebra", "zebras"},
    "giraffe": {"giraffe", "giraffes"},
    "backpack": {"backpack", "backpacks"},
    "umbrella": {"umbrella", "umbrellas"},
    "handbag": {"handbag", "handbags", "purse", "purses"},
    "tie": {"tie", "ties"},
    "suitcase": {"suitcase", "suitcases", "luggage"},
    "frisbee": {"frisbee", "frisbees"},
    "skis": {"ski", "skis"},
    "snowboard": {"snowboard", "snowboards"},
    "sports ball": {"ball", "balls"},
    "kite": {"kite", "kites"},
    "baseball bat": {"baseball bat", "baseball bats", "bat", "bats"},
    "baseball glove": {"baseball glove", "baseball gloves", "glove", "gloves"},
    "skateboard": {"skateboard", "skateboards"},
    "surfboard": {"surfboard", "surfboards"},
    "tennis racket": {"tennis racket", "tennis rackets", "racket", "rackets"},
    "bottle": {"bottle", "bottles"},
    "wine glass": {"wine glass", "wine glasses"},
    "cup": {"cup", "cups"},
    "fork": {"fork", "forks"},
    "knife": {"knife", "knives"},
    "spoon": {"spoon", "spoons"},
    "bowl": {"bowl", "bowls"},
    "banana": {"banana", "bananas"},
    "apple": {"apple", "apples"},
    "sandwich": {"sandwich", "sandwiches"},
    "orange": {"orange", "oranges"},
    "broccoli": {"broccoli"},
    "carrot": {"carrot", "carrots"},
    "hot dog": {"hot dog", "hot dogs"},
    "pizza": {"pizza", "pizzas"},
    "donut": {"donut", "donuts", "doughnut", "doughnuts"},
    "cake": {"cake", "cakes"},
    "chair": {"chair", "chairs"},
    "couch": {"couch", "couches", "sofa", "sofas"},
    "potted plant": {"plant", "plants", "potted plant", "potted plants"},
    "bed": {"bed", "beds"},
    "dining table": {"table", "tables", "dining table", "dining tables"},
    "toilet": {"toilet", "toilets"},
    "tv": {"tv", "tvs", "television", "televisions"},
    "laptop": {"laptop", "laptops"},
    "mouse": {"mouse", "mice"},
    "remote": {"remote", "remotes"},
    "keyboard": {"keyboard", "keyboards"},
    "cell phone": {"cell phone", "cell phones", "phone", "phones"},
    "microwave": {"microwave", "microwaves"},
    "oven": {"oven", "ovens"},
    "toaster": {"toaster", "toasters"},
    "sink": {"sink", "sinks"},
    "refrigerator": {"refrigerator", "refrigerators", "fridge", "fridges"},
    "book": {"book", "books"},
    "clock": {"clock", "clocks"},
    "vase": {"vase", "vases"},
    "scissors": {"scissors"},
    "teddy bear": {"teddy bear", "teddy bears"},
    "hair drier": {"hair drier", "hair driers", "hair dryer", "hair dryers"},
    "toothbrush": {"toothbrush", "toothbrushes"},
}


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(value).lower()).strip()


def question_class(question: str) -> str | None:
    padded = f" {normalize(question)} "
    found: list[tuple[int, str]] = []
    for label, aliases in CLASS_ALIASES.items():
        for alias in aliases:
            if f" {alias} " in padded:
                found.append((len(alias), label))
    return max(found, default=(0, None))[1]


def main() -> None:
    reviewed_ids: set[str] = set()
    reviewed_path = ROOT / "data" / "labeling" / "reviewed.jsonl"
    if reviewed_path.exists():
        for line in reviewed_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                reviewed_ids.add(str(json.loads(line).get("question_id", "")))

    stats = Counter()
    archive_rows = Counter()
    labels = Counter()
    unique_images: set[str] = set()
    eligible_images: set[str] = set()
    eligible_rows: list[dict] = []

    for split in ("train", "test"):
        classified = ROOT / "data" / "metadata" / f"{split}_classified.json"
        source = classified if classified.exists() else ROOT / "data" / "metadata" / f"{split}.json"
        rows = json.loads(source.read_text(encoding="utf-8"))
        stats[f"{split}_rows"] = len(rows)
        for row in rows:
            if row.get("issimple") is not False:
                continue
            stats["complex_total"] += 1
            image = str(row.get("image", ""))
            if image:
                unique_images.add(image)
            try:
                answer = int(row.get("answer"))
            except (TypeError, ValueError):
                continue
            if not 1 <= answer <= 6:
                continue
            stats["complex_numeric_1_4"] += 1
            label = question_class(row.get("question", ""))
            if not label:
                continue
            stats["recognized_class"] += 1
            if not image.startswith(ARCHIVE_PREFIXES):
                continue
            if str(row.get("question_id")) in reviewed_ids:
                stats["already_reviewed"] += 1
                continue
            prefix = image.split("/", 1)[0]
            archive_rows[prefix] += 1
            labels[label] += 1
            eligible_images.add(image)
            eligible_rows.append({
                "question_id": str(row.get("question_id")),
                "image": image,
                "answer": answer,
                "label": label,
                "split": split,
            })

    output = {
        "stats": dict(stats),
        "complex_unique_images": len(unique_images),
        "eligible_rows_before_annotations": len(eligible_rows),
        "eligible_unique_images_before_annotations": len(eligible_images),
        "archive_rows": dict(archive_rows),
        "top_labels": labels.most_common(30),
        "target_requested": 30000,
        "enough_unique_images_before_annotation_qc": len(eligible_images) >= 30000,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
