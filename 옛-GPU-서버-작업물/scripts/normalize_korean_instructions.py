from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FILES = [ROOT / "data/labeling/queue.jsonl", ROOT / "data/labeling/relation_candidates_all.jsonl"]


def normalize(text: str) -> str:
    replacements = {
        "응답 영역": "정답존", "응답영역": "정답존", "응답 구역": "정답존", "응답존": "정답존",
        "답변 영역": "정답존", "답변 구역": "정답존", "답안 영역": "정답존",
        "정답 영역": "정답존", "정답 구역": "정답존",
        "이동시킵니다": "옮기세요", "이동시킵시오": "옮기세요",
        "이동시키십시오": "옮기세요", "이동시키세요": "옮기세요",
        "이동하십시오": "옮기세요", "이동하세요": "옮기세요",
        "Apple": "애플", "apple": "애플", "Wii": "위", "TV": "텔레비전",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    text = re.sub(r"(?:\s*옮기세요){2,}", " 옮기세요", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(".。") + "."


def main() -> None:
    changed = 0
    for path in FILES:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in rows:
            if row.get("translation_status") != "full_sentence_review_ready":
                continue
            before = row["instruction_ko"]
            row["instruction_ko"] = normalize(before)
            changed += before != row["instruction_ko"]
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"normalized_rows_across_files": changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
