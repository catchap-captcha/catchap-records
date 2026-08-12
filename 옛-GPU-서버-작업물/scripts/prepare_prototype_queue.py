from __future__ import annotations

import argparse
import json
from pathlib import Path


INSTRUCTIONS = {
    2318079: "코끼리를 타고 있는 사람을 모두 정답존으로 옮기세요.",
    2315460: "흰색 셔츠를 입고 있는 사람을 모두 정답존으로 옮기세요.",
    2323801: "게임 컨트롤러를 들고 있는 사람을 모두 정답존으로 옮기세요.",
    2344778: "안경을 쓰고 있는 사람을 모두 정답존으로 옮기세요.",
    2367723: "꽃무늬 상의를 입고 있는 사람을 모두 정답존으로 옮기세요.",
    2385155: "프리스비를 들고 있는 사람을 모두 정답존으로 옮기세요.",
    2412854: "회색 셔츠를 입고 있는 사람을 모두 정답존으로 옮기세요.",
    2350294: "프리스비를 들고 있는 사람을 모두 정답존으로 옮기세요.",
    2322285: "파란색 셔츠를 입고 있는 사람을 모두 정답존으로 옮기세요.",
    2363807: "리모컨을 들고 있는 사람을 모두 정답존으로 옮기세요.",
    2407354: "스키를 들고 있는 사람을 모두 정답존으로 옮기세요.",
    2357010: "초록색 옷을 입고 있는 사람을 모두 정답존으로 옮기세요.",
    2389449: "파란색 모자를 쓰고 있는 사람을 모두 정답존으로 옮기세요.",
    2386576: "주황색 티셔츠를 입고 있는 사람을 모두 정답존으로 옮기세요.",
    2333188: "주황색 셔츠를 입고 있는 사람을 모두 정답존으로 옮기세요.",
    2335749: "파란색 코트를 입고 있는 사람을 모두 정답존으로 옮기세요.",
    2323675: "흰색 모자를 쓰고 있는 사람을 모두 정답존으로 옮기세요.",
    2393716: "야구 방망이를 들고 있는 사람을 모두 정답존으로 옮기세요.",
    2333482: "자전거를 타고 있는 사람을 모두 정답존으로 옮기세요.",
    2320701: "커피잔을 들고 있는 사람을 모두 정답존으로 옮기세요.",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    labeling = args.root / "data/labeling"
    source = labeling / "labeling_queue.jsonl"
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = []
    for row in rows:
        image_id = int(row["image_id"])
        if image_id not in INSTRUCTIONS:
            continue
        row["instruction_ko"] = INSTRUCTIONS[image_id]
        row["review_status"] = "pending"
        row["prototype"] = True
        selected.append(row)
    if len(selected) != 20:
        raise RuntimeError(f"Expected 20 prototype rows, got {len(selected)}")
    (labeling / "queue.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected), encoding="utf-8")
    print(json.dumps({"prototype_queue": len(selected), "image_ids": [row["image_id"] for row in selected]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
