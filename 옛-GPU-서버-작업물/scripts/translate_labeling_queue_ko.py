from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SUBJECTS = {
    "people": "사람", "men": "남자", "women": "여자", "kids": "아이",
    "children": "아이", "players": "선수", "baseball players": "야구 선수",
    "giraffes": "기린", "zebras": "얼룩말", "horses": "말", "elephants": "코끼리",
    "dogs": "개", "cats": "고양이", "birds": "새",
}

PHRASES = {
    "yellow shirt": "노란색 셔츠", "a yellow shirt": "노란색 셔츠", "white helmet": "흰색 헬멧",
    "a red coat": "빨간색 코트", "a black coat": "검은색 코트", "red helmet": "빨간색 헬멧",
    "mask": "마스크", "masks": "마스크", "a wine glass wearing glasses": "와인잔",
    "a vest are": "조끼", "a vest": "조끼", "blue jeans": "청바지", "red clothing": "빨간색 옷",
    "grey shirt": "회색 셔츠", "sunglasses": "선글라스", "sunglasses on their body": "선글라스",
    "black skirt": "검은색 치마", "an orange shirt": "주황색 셔츠", "a printed tee shirt": "무늬가 있는 티셔츠",
    "umbrellas on the bridge": "다리 위에서 우산", "purple headbands": "보라색 머리띠",
    "a light blue shirt": "하늘색 셔츠", "white jackets": "흰색 재킷", "bikinis": "비키니",
    "bikini": "비키니", "yellow shorts": "노란색 반바지", "blue jackets": "파란색 재킷",
    "red pants": "빨간색 바지", "tan": "황갈색 옷", "red coats": "빨간색 코트",
    "yellow jackets": "노란색 재킷", "yellow jacket": "노란색 재킷", "black suits": "검은색 정장",
    "long pants": "긴 바지", "pink dresses": "분홍색 원피스", "top hats": "실크해트",
    "a tophat": "실크해트", "red white and black bicycle helmets": "빨강·흰색·검은색 자전거 헬멧",
    "white jerseys": "흰색 유니폼", "on the bus": "버스 안에서 음식", "a black shirt": "검은색 셔츠",
    "black shirt": "검은색 셔츠", "an orange swimsuit": "주황색 수영복", "suits": "정장",
    "down by the window": "창가에", "stripes": "줄무늬 옷", "a green top": "초록색 상의",
    "gloves": "장갑", "a red hat": "빨간색 모자", "an entirely pink outfit": "온통 분홍색인 옷",
    "black hoodies": "검은색 후드티", "yellow helmets": "노란색 헬멧", "drink": "음료",
    "red shoes": "빨간색 신발", "tie": "넥타이", "a surf board": "서핑보드", "a scarf": "목도리",
    "purple": "보라색 옷", "black pants": "검은색 바지", "a yellow board": "노란색 보드",
    "yellow boards": "노란색 보드", "children": "아이", "children in the image": "아이",
    "elephant's nose": "코끼리 코", "a purple shirt": "보라색 셔츠", "purple shirt": "보라색 셔츠",
    "a red jacket": "빨간색 재킷", "a phone": "휴대전화", "up a phone": "휴대전화",
    "sticks": "막대기", "an orange piece of clothing": "주황색 옷", "a black safety helmet": "검은색 안전모",
    "a white and red t shirt": "흰색과 빨간색 티셔츠", "shoes with yellow on them": "노란색이 들어간 신발",
    "a checked top": "체크무늬 상의", "scarfs": "목도리", "controllers": "컨트롤러",
    "at the camera": "카메라", "white dresses": "흰색 원피스", "backpacks": "백팩",
    "red umbrella": "빨간색 우산", "black t-shirts": "검은색 티셔츠", "a visor": "선캡",
    "kites": "연", "bright blue shirts": "밝은 파란색 셔츠", "a skirt": "치마",
    "short sleeve shirts": "반소매 셔츠", "down": "", "a helmet": "헬멧", "yellow": "노란색 옷",
    "watch": "손목시계", "a tennis racquet": "테니스 라켓", "up a giant soccer ball": "커다란 축구공",
    "a black down vest": "검은색 패딩 조끼", "a gray jacket": "회색 재킷", "a dark shirt": "어두운색 셔츠",
    "white pants": "흰색 바지", "green clothes": "초록색 옷", "blue and white umbrellas": "파란색과 흰색 우산",
    "an orange hat": "주황색 모자", "party hats": "고깔모자", "a red baseball hat": "빨간색 야구 모자",
    "yellow robes": "노란색 가운", "a red top": "빨간색 상의", "white t shirt": "흰색 티셔츠",
    "a baseball bat": "야구 방망이", "baseball bats": "야구 방망이", "a bat": "방망이", "bats": "방망이",
    "game controllers": "게임 컨트롤러", "money bags": "돈 가방", "a hot dog": "핫도그",
    "a bag of chips": "감자칩 봉지", "a wine glass": "와인잔", "a glass of wine": "와인잔",
    "tennis rackets": "테니스 라켓", "tennis bat": "테니스 라켓", "flying discs": "플라잉 디스크",
    "a coffee cup": "커피잔", "coffee cup": "커피잔", "a camera": "카메라", "a knife": "칼",
    "a cake": "케이크", "a dog": "개", "a horse": "말", "a mop": "대걸레", "a book": "책", "book": "책",
    "book in their hand": "책", "book in her hand": "책", "book in his hand": "책",
    "surfboards": "서핑보드", "snowboards": "스노보드", "skis": "스키", "skiis": "스키",
    "frisbees": "프리스비", "a frisbee": "프리스비", "umbrellas": "우산", "an umbrella": "우산",
    "remotes": "리모컨", "plates": "접시", "scissors": "가위", "bags": "가방", "a backpack": "백팩",
    "baskets": "바구니", "a leather briefcase": "가죽 서류가방", "red flower bouquets": "빨간 꽃다발",
    "yellow bags": "노란 가방", "a yellow shopping bag": "노란 쇼핑백", "a load on their shoulder": "어깨 위의 짐",
    "a load on his shoulder": "어깨 위의 짐", "a load on her shoulder": "어깨 위의 짐",
    "instruments": "악기", "an instrument": "악기", "the camera": "카메라",
    "a bicycle": "자전거", "bicycles": "자전거", "a bike": "자전거", "bike": "자전거",
    "the grass": "풀", "grass": "풀", "off the ground": "땅에 있는 먹이", "a doughnut": "도넛",
    "orange hats": "주황색 모자", "blue shoes": "파란색 신발", "pink shirt": "분홍색 셔츠",
    "pink shirts": "분홍색 셔츠", "a dress": "원피스", "green shirt": "초록색 셔츠",
    "a green shirt": "초록색 셔츠", "red shirts": "빨간색 셔츠", "a red shirt": "빨간색 셔츠",
    "red shirt": "빨간색 셔츠", "blue shirt": "파란색 셔츠", "a blue shirt": "파란색 셔츠",
    "white shirt": "흰색 셔츠", "a white shirt": "흰색 셔츠", "gray shirt": "회색 셔츠",
    "a gray shirt": "회색 셔츠", "black shorts": "검은색 반바지", "shorts": "반바지",
    "long boots": "긴 부츠", "blue cap": "파란색 모자", "a blue hat": "파란색 모자",
    "blue hats": "파란색 모자", "a black top": "검은색 상의", "black top": "검은색 상의",
    "orange tee shirt": "주황색 티셔츠", "an orange tee shirt": "주황색 티셔츠",
    "the orange shirt": "주황색 셔츠", "orange shirt": "주황색 셔츠", "a blue coat": "파란색 코트",
    "white hat": "흰색 모자", "a white hat": "흰색 모자", "hats": "모자", "a hat": "모자", "hat": "모자",
    "helmets": "헬멧", "glasses": "안경", "scarves": "목도리", "shirts": "셔츠",
    "lime green": "연두색 옷", "blue": "파란색 옷", "green": "초록색 옷", "red": "빨간색 옷",
    "black": "검은색 옷", "white": "흰색 옷", "orange": "주황색 옷", "pink": "분홍색 옷",
    "on a toilet": "변기에", "on the snow": "눈 위에", "on the ground": "바닥에", "on the floor": "바닥에",
    "on a bike": "자전거에", "by the window": "창가에", "on the chair": "의자에", "inside the house": "집 안에",
    "under an open purple umbrella": "펼쳐진 보라색 우산 아래에", "under and open purple umbrella": "펼쳐진 보라색 우산 아래에",
    "on the wall": "벽에", "behind the backstop": "백스톱 뒤에", "at the tables": "탁자에", "still": "가만히",
    "under a tent": "텐트 아래에", "under the lit digital reader sign on the bus": "버스의 불이 켜진 전광판 아래에",
    "in front of the bar": "바 앞에", "in the grass": "풀밭에", "by the wall": "벽 옆에",
    "to the left of the woman holding a wine glass": "와인잔을 든 여성의 왼쪽에", "up": "똑바로",
    "near the refrigerator": "냉장고 근처에", "on two legs": "두 다리로", "to the right of the bus": "버스 오른쪽에",
    "next to the horse": "말 옆에", "straight": "똑바로", "on the center of the room": "방 중앙을",
    "under the umbrella": "우산 아래를",
}


REMOVE = (
    "in the image", "in this image", "in the picture", "in this picture", "in the photo", "in this photo",
    "there are zebras not eating grass too", "there are zebras not eating too", "in their hands", "in their hand", "in his hand", "in her hand",
)


def clean(value: str) -> str:
    value = value.lower().strip(" ?.!")
    for phrase in REMOVE:
        value = value.replace(phrase, "")
    return re.sub(r"\s+", " ", value).strip(" ,?")


def korean_phrase(value: str) -> tuple[str, bool]:
    value = clean(value)
    if not value:
        return "", True
    if value in PHRASES:
        return PHRASES[value], True
    translated = value
    for source, target in sorted(PHRASES.items(), key=lambda item: len(item[0]), reverse=True):
        translated = re.sub(rf"\b{re.escape(source)}\b", target, translated)
    translated = clean(translated)
    return translated, not bool(re.search(r"[a-z]", translated))


def particle(word: str, consonant: str, vowel: str) -> str:
    last = ord(word[-1]) if word else 0
    if 0xAC00 <= last <= 0xD7A3:
        return consonant if (last - 0xAC00) % 28 else vowel
    return vowel


def translate(row: dict) -> tuple[str, bool]:
    subject = SUBJECTS.get(row.get("target_label", ""), "객체")
    qualifier, complete = korean_phrase(row.get("qualifier", ""))
    action = row.get("action")
    if action == "wearing":
        text = f"{qualifier}{particle(qualifier, '을', '를')} 착용한 {subject}을 모두 정답존으로 옮기세요."
    elif action == "holding":
        text = f"{qualifier}{particle(qualifier, '을', '를')} 들고 있는 {subject}을 모두 정답존으로 옮기세요."
    elif action == "carrying":
        text = f"{qualifier}{particle(qualifier, '을', '를')} 운반하고 있는 {subject}을 모두 정답존으로 옮기세요."
    elif action == "riding":
        text = f"{qualifier}{particle(qualifier, '을', '를')} 타고 있는 {subject}을 모두 정답존으로 옮기세요."
    elif action == "eating":
        text = f"{qualifier}{particle(qualifier, '을', '를')} 먹고 있는 {subject}을 모두 정답존으로 옮기세요."
    elif action == "looking":
        text = f"{qualifier}{particle(qualifier, '을', '를')} 바라보는 {subject}을 모두 정답존으로 옮기세요."
    elif action == "touching":
        text = f"{qualifier}{particle(qualifier, '을', '를')} 만지고 있는 {subject}을 모두 정답존으로 옮기세요."
    elif action == "playing":
        text = f"{qualifier}{particle(qualifier, '을', '를')} 연주하고 있는 {subject}을 모두 정답존으로 옮기세요."
    elif action == "sitting":
        text = f"{qualifier} 앉아 있는 {subject}을 모두 정답존으로 옮기세요."
    elif action == "standing":
        text = f"{qualifier} 서 있는 {subject}을 모두 정답존으로 옮기세요."
    elif action == "walking":
        text = f"{qualifier} 걷고 있는 {subject}을 모두 정답존으로 옮기세요."
    else:
        text, complete = f"조건에 해당하는 {subject}을 모두 정답존으로 옮기세요.", False
    text = re.sub(r"\s+", " ", text).replace("객체을", "객체를").replace("여자을", "여자를").replace("남자을", "남자를").replace("아이을", "아이를")
    return text, complete


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = args.root / "data/labeling/queue.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    incomplete = []
    for row in rows:
        row["instruction_ko"], complete = translate(row)
        row["translation_status"] = "auto_translated" if complete else "needs_translation_review"
        if not complete:
            incomplete.append({"queue_id": row["queue_id"], "question_en": row["question_en"],
                               "instruction_ko": row["instruction_ko"]})
    if not args.dry_run:
        backup = path.with_name("queue.before_ko_translation.jsonl")
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        (path.parent / "translation_review.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in incomplete), encoding="utf-8")
    print(json.dumps({"translated": len(rows), "complete": len(rows) - len(incomplete),
                      "needs_review": len(incomplete), "dry_run": args.dry_run}, ensure_ascii=False))
    for row in incomplete[:30]:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
