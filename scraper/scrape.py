# -*- coding: utf-8 -*-
"""
스타대학(pan_monstarz) 월간 활동 수집기.

지표 4종을 회원번호 기준으로 집계한다:
  posts      : 해당 월에 작성한 게시글 수
  recv_good  : 본인 글이 받은 추천 합계
  comments   : 해당 월 글에 작성한 댓글 수
  given_good : 해당 월 글에 누른 추천 수 (글별 추천인 목록 기준)

모드:
  backfill : 지정한 월을 글번호 내림차순으로 전부 훑는다(정확·무거움). 5월용. 중단되면 이어서 재개.
  update   : 마지막 처리 지점 이후의 새 글만 수집(가벼움). GitHub Actions 매일용.

외부 라이브러리 없음(표준 라이브러리만). Python 3.9+.
"""
import argparse, json, os, re, sys, time, io, random
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

BOARD = "pan_monstarz"
BASE = "https://ygosu.com"
KST = timezone(timedelta(hours=9))
UA = "monstarz-ranking/1.0 (community activity leaderboard; +https://github.com/)"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "data"))
PUB_DIR = os.path.normpath(os.path.join(HERE, "..", "docs", "data"))

# ---- 파싱 정규식 -------------------------------------------------------------
RE_DATE = re.compile(r'<div class="date">\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
RE_NICK = re.compile(r"show_nick_dropdown\(\$\(this\),\s*'[^']*',\s*'(\d+)'[^>]*>(.*?)</a>", re.S)

def clean_nick(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\xa0", " ").replace("&nbsp;", " ")
    return s.strip()

# ---- HTTP -------------------------------------------------------------------
def http(url, data=None, referer=None, tries=4):
    headers = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
    if referer:
        headers["Referer"] = referer
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["X-Requested-With"] = "XMLHttpRequest"
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", "replace"), r.status
        except urllib.error.HTTPError as e:
            if e.code in (404, 403, 410):
                return None, e.code
            wait = min(30, 2 ** i) + random.random()
            print(f"    HTTP {e.code} {url} -> retry {i+1} in {wait:.1f}s", flush=True)
            time.sleep(wait)
        except Exception as e:
            wait = min(30, 2 ** i) + random.random()
            print(f"    ERR {type(e).__name__} {url} -> retry {i+1} in {wait:.1f}s", flush=True)
            time.sleep(wait)
    return None, -1

# ---- 글/추천인 파싱 ----------------------------------------------------------
def parse_post(pid, html):
    """유효한 pan_monstarz 글이면 dict, 아니면 None(삭제/타 게시판/블라인드)."""
    if html is None:
        return None
    good_token = f"board_{BOARD}_{pid}_good"
    if good_token not in html or "id='contain_user_info'" not in html:
        return None
    md = RE_DATE.search(html)
    if not md:
        return None
    date = md.group(1)

    # 작성자: contain_user_info 블록의 첫 닉네임
    after = html.split("id='contain_user_info'", 1)[1][:600]
    ma = RE_NICK.search(after)
    if not ma:
        return None
    author_no, author_nick = ma.group(1), clean_nick(ma.group(2))

    # 추천 수
    mg = re.search(re.escape(good_token) + r"[^>]*>\s*([0-9,]+)", html)
    good = int(mg.group(1).replace(",", "")) if mg else 0

    # 댓글 작성자: reply_list_layer 구간만 (best_reply 중복 제외)
    comments = []
    seg = html.split("id='reply_list_layer'", 1)
    if len(seg) > 1:
        body = seg[1].split("reply_paging", 1)[0]
        for m in RE_NICK.finditer(body):
            comments.append((m.group(1), clean_nick(m.group(2))))

    return {"id": pid, "date": date, "author_no": author_no,
            "author_nick": author_nick, "good": good, "comments": comments}

def fetch_voters(pid):
    """글 추천인 목록 -> [(member_no, nick), ...]. 실패 시 None."""
    url = f"{BASE}/action.yg"
    referer = f"{BASE}/board/{BOARD}/{pid}"
    html, st = http(url, data={"path": "board/get_vote_list", "bid": BOARD,
                               "idx": str(pid), "return_url": referer}, referer=referer)
    if html is None:
        return None
    try:
        j = json.loads(html)
    except Exception:
        return None
    if j.get("msg") != "SUCCESS":
        return []
    return [(m.group(1), clean_nick(m.group(2))) for m in RE_NICK.finditer(j.get("html", "") or "")]

# ---- 집계 상태 ---------------------------------------------------------------
def empty_member():
    return {"nick": "", "posts": 0, "recv_good": 0, "comments": 0, "given_good": 0}

def state_path(month):
    return os.path.join(DATA_DIR, f"state_{month}.json")

def load_state(month):
    p = state_path(month)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"month": month, "cursor": None, "done": False, "start_id": None,
            "counters": {"posts": 0, "ids_walked": 0, "deleted": 0, "voter_fetches": 0},
            "members": {}}

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)

def member(state, no):
    m = state["members"].get(no)
    if m is None:
        m = empty_member()
        state["members"][no] = m
    return m

def fold(state, rec, with_voters, delay):
    """파싱된 글 1건을 집계에 반영. 추천인 목록 fetch 여부 반환(요청수 계산용)."""
    a = member(state, rec["author_no"])
    a["nick"] = rec["author_nick"] or a["nick"]
    a["posts"] += 1
    a["recv_good"] += rec["good"]
    for cno, cnick in rec["comments"]:
        m = member(state, cno)
        if cnick:
            m["nick"] = cnick
        m["comments"] += 1
    fetched = False
    if with_voters and rec["good"] > 0:
        voters = fetch_voters(rec["id"])
        fetched = True
        state["counters"]["voter_fetches"] += 1
        if voters:
            for vno, vnick in voters:
                m = member(state, vno)
                if vnick:
                    m["nick"] = vnick
                m["given_good"] += 1
        time.sleep(delay)
    return fetched

# ---- 게시판 최신 글번호 ------------------------------------------------------
def current_max_id():
    html, st = http(f"{BASE}/board/{BOARD}")
    if not html:
        return None
    ids = [int(x) for x in re.findall(rf"/board/{BOARD}/(\d+)", html)]
    ids = [i for i in ids if i > 1_000_000]
    return max(ids) if ids else None

# ---- backfill (지정 월 전체) -------------------------------------------------
def run_backfill(month, start_id, delay, with_voters, max_minutes):
    y, mo = map(int, month.split("-"))
    lo_str = f"{y:04d}-{mo:02d}-01 00:00:00"
    ny, nmo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    hi_str = f"{ny:04d}-{nmo:02d}-01 00:00:00"

    state = load_state(month)
    if state.get("start_id") is None:
        state["start_id"] = start_id
    if state["done"]:
        print(f"[{month}] 이미 완료됨. (members={len(state['members'])})")
        return state
    pid = state["cursor"] if state["cursor"] is not None else start_id
    floor_id = state["start_id"] - 300_000
    t0 = time.time()
    batch = 0
    print(f"[{month}] backfill 시작 pid={pid} 범위[{lo_str} ~ {hi_str}) delay={delay} voters={with_voters}")
    while pid >= floor_id:
        html, st = http(f"{BASE}/board/{BOARD}/{pid}")
        state["counters"]["ids_walked"] += 1
        rec = parse_post(pid, html)
        if rec is None:
            state["counters"]["deleted"] += 1
        else:
            d = rec["date"]
            state["last_date"] = d
            if d >= hi_str:
                pass  # 아직 다음달(범위 위) — 계속 내려감
            elif d < lo_str:
                print(f"[{month}] {d} (id={pid}) 도달 — 이전 달 진입, 종료")
                state["done"] = True
                break
            else:
                if state.get("top_may_id") is None:
                    state["top_may_id"] = pid   # 이 달 최상단 글번호(이후 forward 수집 핸드오프 기준)
                fetched = fold(state, rec, with_voters, delay)
                state["counters"]["posts"] += 1
        pid -= 1
        state["cursor"] = pid
        time.sleep(delay)
        batch += 1
        if batch % 40 == 0:
            save_json(state_path(month), state)
            c = state["counters"]
            print(f"  진행 pid={pid} posts={c['posts']} walked={c['ids_walked']} "
                  f"voters={c['voter_fetches']} members={len(state['members'])}", flush=True)
        if max_minutes and (time.time() - t0) / 60 >= max_minutes:
            print(f"[{month}] 시간 제한({max_minutes}분) 도달 — 저장 후 중단(재실행하면 이어서)")
            break
    save_json(state_path(month), state)
    if state["done"]:
        # 다음(forward) 수집 핸드오프: 이 달 최상단 글 위부터 update가 이어받게
        handoff = state.get("top_may_id") or state["start_id"]
        save_json(os.path.join(DATA_DIR, "cursor.json"), {"last_id": handoff})
        print(f"[{month}] 완료 posts={state['counters']['posts']} members={len(state['members'])}")
    return state

# ---- update (마지막 이후 새 글) ---------------------------------------------
def run_update(delay, with_voters, max_minutes):
    cpath = os.path.join(DATA_DIR, "cursor.json")
    mx = current_max_id()
    if mx is None:
        print("최신 글번호를 못 읽음 — 중단")
        return
    if not os.path.exists(cpath):
        save_json(cpath, {"last_id": mx})
        print(f"커서 초기화 last_id={mx} (다음 실행부터 신규 글 수집)")
        return
    last = json.load(open(cpath, encoding="utf-8"))["last_id"]
    if mx <= last:
        print(f"새 글 없음 (last={last}, max={mx})")
        return
    print(f"update: {last+1} ~ {mx} ({mx-last}개 후보) delay={delay} voters={with_voters}")
    t0 = time.time()
    cache = {}        # month -> state
    touched = set()
    pid = last + 1
    processed = 0
    while pid <= mx:
        html, st = http(f"{BASE}/board/{BOARD}/{pid}")
        rec = parse_post(pid, html)
        if rec is not None:
            month = rec["date"][:7]
            if month not in cache:
                cache[month] = load_state(month)
            fold(cache[month], rec, with_voters, delay)
            cache[month]["counters"]["posts"] += 1
            touched.add(month)
            processed += 1
        save_json(cpath, {"last_id": pid})
        pid += 1
        time.sleep(delay)
        if processed and processed % 40 == 0:
            for m in touched:
                save_json(state_path(m), cache[m])
            print(f"  진행 pid={pid} processed={processed}", flush=True)
        if max_minutes and (time.time() - t0) / 60 >= max_minutes:
            print(f"시간 제한({max_minutes}분) 도달 — 저장 후 중단")
            break
    for m in touched:
        save_json(state_path(m), cache[m])
    print(f"update 완료 processed={processed} touched={sorted(touched)}")

# ---- CLI --------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="스타대학 월간 활동 수집기")
    sub = ap.add_subparsers(dest="mode", required=True)

    b = sub.add_parser("backfill", help="지정 월 전체 수집(내림차순)")
    b.add_argument("--month", required=True, help="예: 2026-05")
    b.add_argument("--start-id", type=int, required=True, help="해당 월 직후(다음달 초) 글번호 근처에서 시작")
    b.add_argument("--delay", type=float, default=0.4)
    b.add_argument("--no-voters", action="store_true", help="'추천 누른 사람' 집계 생략(요청 절반)")
    b.add_argument("--max-minutes", type=float, default=0, help="이 시간 지나면 저장 후 중단(0=무제한)")

    u = sub.add_parser("update", help="마지막 이후 새 글만 수집(Actions용)")
    u.add_argument("--delay", type=float, default=0.4)
    u.add_argument("--no-voters", action="store_true")
    u.add_argument("--max-minutes", type=float, default=300)

    a = ap.parse_args()
    os.makedirs(DATA_DIR, exist_ok=True)
    if a.mode == "backfill":
        run_backfill(a.month, a.start_id, a.delay, not a.no_voters, a.max_minutes)
    elif a.mode == "update":
        run_update(a.delay, not a.no_voters, a.max_minutes)

if __name__ == "__main__":
    main()
