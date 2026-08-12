from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


ARCHIVES = {"VG_100K": "images.zip", "VG_100K_2": "images2.zip"}
ACTION_LABELS = {
    "wearing": "입고 있는",
    "holding": "들고 있는",
    "riding": "타고 있는",
    "sitting": "앉아 있는",
    "standing": "서 있는",
    "eating": "먹고 있는",
    "carrying": "운반하고 있는",
    "looking": "바라보고 있는",
    "touching": "만지고 있는",
    "walking": "걷고 있는",
    "playing": "놀고 있는",
}
TARGET_ALIASES = {
    "people": {"person", "people", "man", "woman", "boy", "girl"},
    "men": {"man", "men"}, "women": {"woman", "women"},
    "children": {"child", "children", "boy", "girl"},
    "kids": {"child", "kid", "kids", "boy", "girl"},
    "players": {"player", "players"}, "baseball players": {"player", "baseball player"},
    "giraffes": {"giraffe", "giraffes"}, "elephants": {"elephant", "elephants"},
    "zebras": {"zebra", "zebras"}, "horses": {"horse", "horses"},
    "dogs": {"dog", "dogs"}, "cats": {"cat", "cats"}, "birds": {"bird", "birds"},
}


def read_zip_json(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".json"))
        with archive.open(name) as source:
            return json.load(io.TextIOWrapper(source, encoding="utf-8"))


def names(obj: dict[str, Any]) -> list[str]:
    raw = obj.get("names") or [obj.get("name", "")]
    return [str(value).strip().lower() for value in raw if value]


def parse_question(row: dict[str, Any]) -> tuple[str, str, str] | None:
    question = str(row.get("question", "")).lower().strip(" ?")
    match = re.match(r"how many (.+?) (?:are|is) (wearing|holding|riding|sitting|standing|eating|carrying|looking|touching|walking|playing)\b(.*)", question)
    if not match:
        return None
    target = match.group(1).strip()
    target = re.sub(r"^(?:of the|of those|of these) ", "", target)
    target = re.sub(r" (?:in (?:this|the) (?:image|picture|photo))$", "", target)
    if target not in TARGET_ALIASES:
        return None
    return target, match.group(2), match.group(3).strip()


def object_box(obj: dict[str, Any], width: int, height: int) -> dict[str, Any] | None:
    x, y = float(obj.get("x", 0)), float(obj.get("y", 0))
    w = float(obj.get("w", obj.get("width", 0)))
    h = float(obj.get("h", obj.get("height", 0)))
    if min(w, h) <= 0 or x < 0 or y < 0 or x + w > width + 2 or y + h > height + 2:
        return None
    return {"object_key": str(obj.get("object_id")), "label": names(obj)[0],
            "x": x / width, "y": y / height, "width": w / width, "height": h / height,
            "area_ratio": w * h / (width * height)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", default="labeling_queue.jsonl")
    args = parser.parse_args()
    root = args.root
    labeling = root / "data/labeling"
    images_dir = labeling / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    rows = json.loads((root / "data/metadata/test.json").read_text(encoding="utf-8"))
    candidates = []
    for row in rows:
        try:
            answer = int(row.get("answer"))
        except (TypeError, ValueError):
            continue
        parsed = parse_question(row)
        if row.get("issimple") is False and 1 <= answer <= 4 and parsed and str(row.get("image", "")).startswith("VG_"):
            candidates.append({**row, "answer": answer, "parsed": parsed})

    ids = {int(Path(row["image"]).stem) for row in candidates}
    vg = root / "data/annotations/visual_genome"
    object_data = {int(row["image_id"]): row.get("objects", []) for row in read_zip_json(vg / "objects.json.zip") if int(row["image_id"]) in ids}
    relation_data = {int(row["image_id"]): row.get("relationships", []) for row in read_zip_json(vg / "relationships.json.zip") if int(row["image_id"]) in ids}
    image_data = {int(row["image_id"]): row for row in read_zip_json(vg / "image_data.json.zip") if int(row["image_id"]) in ids}
    archives = {name: zipfile.ZipFile(root / "data/raw" / filename) for name, filename in ARCHIVES.items()}
    queue: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    used_images: set[int] = set()
    try:
        for candidate in candidates:
            image_id = int(Path(candidate["image"]).stem)
            if image_id in used_images:
                continue
            target_name, action, qualifier = candidate["parsed"]
            meta = image_data.get(image_id, {})
            width, height = int(meta.get("width", 0)), int(meta.get("height", 0))
            aliases = TARGET_ALIASES[target_name]
            objects = []
            for obj in object_data.get(image_id, []):
                if not aliases.intersection(names(obj)):
                    continue
                box = object_box(obj, width, height) if width and height else None
                if box and box["area_ratio"] >= 0.006:
                    objects.append(box)
            object_ids = {box["object_key"] for box in objects}
            target_ids: set[str] = set()
            hints = []
            for relation in relation_data.get(image_id, []):
                predicate = str(relation.get("predicate", "")).lower().strip()
                subject_id = str(relation.get("subject", {}).get("object_id"))
                if subject_id in object_ids and action in predicate:
                    target_ids.add(subject_id)
                    hints.append({"subject_id": subject_id, "predicate": predicate,
                                  "object": names(relation.get("object", {}))})
            reason = None
            if len(objects) < 2: reason = "not_enough_same_type_objects"
            elif len(objects) > 8: reason = "too_many_objects"
            elif len(target_ids) != candidate["answer"]: reason = "relationship_count_mismatch"
            elif len(objects) <= len(target_ids): reason = "no_decoy"
            if reason:
                rejected.append({"question_id": candidate["question_id"], "image_id": image_id, "reason": reason})
                continue
            root_name = candidate["image"].split("/", 1)[0]
            try:
                payload = archives[root_name].read(candidate["image"])
            except KeyError:
                continue
            output_name = f"{image_id}.jpg"
            with Image.open(io.BytesIO(payload)) as image:
                ImageOps.exif_transpose(image).convert("RGB").save(images_dir / output_name, "JPEG", quality=94, optimize=True)
            instruction = f"{ACTION_LABELS[action]} {target_name} 객체를 모두 정답존으로 옮기세요."
            queue.append({"queue_id": f"tallyqa_{candidate['question_id']}", "question_id": candidate["question_id"],
                          "image_id": image_id, "image_path": f"images/{output_name}",
                          "question_en": candidate["question"], "instruction_ko": instruction,
                          "expected_target_count": candidate["answer"], "source": "visual_genome",
                          "split": "test", "target_label": target_name, "action": action,
                          "qualifier": qualifier, "relationship_hints": hints,
                          "objects": [{**box, "role": "target" if box["object_key"] in target_ids else "decoy"} for box in objects],
                          "review_status": "pending", "difficulty": 2})
            used_images.add(image_id)
            if len(queue) >= args.limit:
                break
    finally:
        for archive in archives.values():
            archive.close()
    output = labeling / args.output
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queue), encoding="utf-8")
    (labeling / "relation_rejected.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rejected), encoding="utf-8")
    print(json.dumps({"complex_candidates": len(candidates), "queued": len(queue), "rejected": len(rejected), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
