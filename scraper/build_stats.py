# -*- coding: utf-8 -*-
"""
data/state_*.json (수집기가 만든 집계 상태) -> docs/data/monstarz_<월>.json (화면용).
또 docs/data/months.json (월 목록)도 갱신한다. 표준 라이브러리만 사용.
"""
import json, os, glob, re
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "data"))
PUB_DIR = os.path.normpath(os.path.join(HERE, "..", "docs", "data"))
SOURCE = "https://ygosu.com/board/pan_monstarz"
METRICS = ["posts", "recv_good", "comments", "given_good"]
TOP_N = 20

def label(month):
    y, m = month.split("-")
    return f"{int(y)}년 {int(m)}월"

def to_dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")

def progress_pct(state):
    if state.get("done"):
        return 100
    ld = state.get("last_date")
    month = state.get("month")
    if not ld or not month:
        return 0
    y, mo = map(int, month.split("-"))
    lo = datetime(y, mo, 1)
    hi = datetime(y + 1, 1, 1) if mo == 12 else datetime(y, mo + 1, 1)
    try:
        cur = to_dt(ld)
    except Exception:
        return 0
    frac = (hi - cur).total_seconds() / (hi - lo).total_seconds()
    return max(1, min(99, round(frac * 100)))

def rank(members, key):
    rows = [(no, m) for no, m in members.items() if m.get(key, 0) > 0]
    # 값 내림차순, 동률은 글수 -> 닉네임 순으로 안정 정렬
    rows.sort(key=lambda x: (-x[1][key], -x[1].get("posts", 0), x[1].get("nick", "")))
    out = []
    for i, (no, m) in enumerate(rows[:TOP_N], 1):
        out.append({"rank": i, "no": no, "nick": m.get("nick") or f"#{no}", "value": m[key]})
    return out

def build_one(state):
    month = state["month"]
    members = state.get("members", {})
    c = state.get("counters", {})
    return {
        "month": month,
        "month_label": label(month),
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": SOURCE,
        "collection": {
            "complete": bool(state.get("done")),
            "progress_pct": progress_pct(state),
            "posts_total": c.get("posts", 0),
            "members_total": len(members),
            "ids_walked": c.get("ids_walked", 0),
            "voter_fetches": c.get("voter_fetches", 0),
        },
        "boards": {k: rank(members, k) for k in METRICS},
    }

def main():
    os.makedirs(PUB_DIR, exist_ok=True)
    states = sorted(glob.glob(os.path.join(DATA_DIR, "state_*.json")))
    months = []
    for sp in states:
        with open(sp, encoding="utf-8") as f:
            state = json.load(f)
        pub = build_one(state)
        out = os.path.join(PUB_DIR, f"monstarz_{state['month']}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(pub, f, ensure_ascii=False, separators=(",", ":"))
        months.append({"month": state["month"], "label": label(state["month"])})
        print(f"wrote {out}  (members={pub['collection']['members_total']}, "
              f"posts={pub['collection']['posts_total']}, {pub['collection']['progress_pct']}%)")
    months.sort(key=lambda x: x["month"], reverse=True)
    with open(os.path.join(PUB_DIR, "months.json"), "w", encoding="utf-8") as f:
        json.dump({"months": months}, f, ensure_ascii=False)
    print(f"wrote months.json ({len(months)} months)")

if __name__ == "__main__":
    main()
