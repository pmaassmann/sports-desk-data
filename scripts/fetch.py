"""Daily download of the Sports Desk ranking lists.

Runs on GitHub Actions (which has open internet). Each source becomes one
JSON file in data/, already in the exact shape of the hub's nfl/raw_<name>
documents ({asOf, source, sourceDate, count, rows, ...}), so the cloud pull
can copy them into the hub byte-for-byte.

A source that fails, or comes back far too short, keeps yesterday's file and
is reported in data/status.json instead of overwriting good data.
"""

import datetime as dt
import json
import re
import sys
import traceback
from pathlib import Path

import requests

SEASON = 2026
DATA = Path(__file__).resolve().parent.parent / "data"
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
POS = ("QB", "RB", "WR", "TE")
# Minimum row counts the hub's rankings rebuild expects (hub/lessons).
MINIMUM = {
    "raw_fc_dynasty": 380,
    "raw_fc_redraft": 150,
    "raw_ktc": 400,
    "raw_sleeper_season": 400,
    "raw_espn_ros": 400,
    "raw_fp_flex": 200,
}

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")
TODAY = NOW.date().isoformat()


def get(url, **kw):
    headers = dict(UA)
    headers.update(kw.pop("headers", {}))
    r = requests.get(url, headers=headers, timeout=45, **kw)
    r.raise_for_status()
    return r


def clean(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.1f}" if abs(v - round(v)) > 1e-9 else str(int(round(v)))
    return str(v).replace("|", "/").replace("\n", " ").strip()


def table(header, rows):
    return "\n".join([header] + ["|".join(clean(c) for c in r) for r in rows])


def doc(source, source_date, header, rows, **extra):
    d = {
        "asOf": NOW_ISO,
        "source": source,
        "sourceDate": source_date,
        "count": len(rows),
        "rows": table(header, rows),
        "fetchedBy": "GitHub Actions (pmaassmann/sports-desk-data)",
    }
    d.update(extra)
    return d


# ---------------------------------------------------------------- FantasyCalc
def fantasycalc(dynasty):
    url = (
        "https://api.fantasycalc.com/values/current?isDynasty="
        f"{'true' if dynasty else 'false'}&numQbs=1&numTeams={10 if dynasty else 12}&ppr=1"
    )
    items = get(url).json()
    rows = []
    for it in items:
        p = it.get("player") or {}
        pos = p.get("position")
        if pos not in POS:
            continue
        value = it.get("value") if dynasty else (it.get("redraftValue") or it.get("value"))
        if dynasty:
            age = p.get("maybeAge")
            rows.append([p.get("name"), pos, p.get("maybeTeam"),
                         round(age, 1) if isinstance(age, (int, float)) else "",
                         p.get("sleeperId"), value, it.get("overallRank"), it.get("positionRank")])
        else:
            rows.append([p.get("name"), pos, p.get("maybeTeam"), value,
                         it.get("overallRank"), it.get("positionRank")])
    rows.sort(key=lambda r: (r[-2] if isinstance(r[-2], (int, float)) else 9999))
    if dynasty:
        return doc("FantasyCalc dynasty (1QB, 10 teams, PPR)", TODAY,
                   "name|pos|team|age|sleeperId|value|overallRank|positionRank", rows)
    return doc("FantasyCalc redraft (1QB, 12 teams, PPR)", TODAY,
               "name|pos|team|value|overallRank|positionRank", rows)


# ------------------------------------------------------------- KeepTradeCut
def ktc():
    r = requests.get("https://keeptradecut.com/dynasty-rankings?page=0&filters=QB|WR|RB|TE|RDP&format=1",
                     headers=dict(UA, Accept="text/html,application/xhtml+xml"), timeout=45)
    html = r.text
    players = None
    for pat in (r"var\s+playersArray\s*=\s*(\[.*?\]);\s*\n", r"playersArray\s*=\s*(\[.*?\]);",
                r"var\s+playersArray\s*=\s*(\[.*\])\s*;"):
        m = re.search(pat, html, re.S)
        if m:
            try:
                players = json.loads(m.group(1))
                break
            except ValueError:
                continue
    if not players:
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        hint = "cloudflare" if "cf-" in html or "Just a moment" in html else ""
        raise RuntimeError(f"no player list on KTC page: HTTP {r.status_code}, {len(html)} bytes, "
                           f"title={title.group(1).strip()[:60] if title else None!r}, "
                           f"mentions playersArray={'playersArray' in html} {hint}")
    rows, picks = [], []
    for p in players:
        one = p.get("oneQBValues") or {}
        name, pos = p.get("playerName"), p.get("position")
        if pos == "RDP" or (pos not in POS and re.match(r"^20\d\d ", name or "")):
            picks.append([name, one.get("value")])
        elif pos in POS:
            age = p.get("age")
            rows.append([name, pos, p.get("team"),
                         round(age, 1) if isinstance(age, (int, float)) else "",
                         one.get("value"), one.get("rank")])
    rows.sort(key=lambda r: -(r[4] or 0))
    for i, r in enumerate(rows, 1):
        if not r[5]:
            r[5] = i
    picks.sort(key=lambda r: -(r[1] or 0))
    return doc("KeepTradeCut dynasty rankings (1QB)", TODAY,
               "name|pos|team|age|oneQBValue|oneQBRank", rows,
               pickCount=len(picks), pickRows=table("name|oneQBValue", picks))


# ------------------------------------------------------------------ Sleeper
SLEEPER_FIELDS = ("gp", "pts_ppr", "off_snp", "tm_off_snp", "rec_tgt", "rush_att")


def sleeper_season():
    q = "season_type=regular&position[]=QB&position[]=RB&position[]=WR&position[]=TE&order_by=pts_ppr"
    agg = {}
    try:
        data = get(f"https://api.sleeper.com/stats/nfl/{SEASON}?{q}").json()
        for e in data:
            agg[e["player_id"]] = e
        how = "season endpoint"
    except Exception:
        data = None
    if not agg:
        # Fallback: add up every played week.
        state = get("https://api.sleeper.app/v1/state/nfl").json()
        last = int(state.get("week") or 1)
        for w in range(1, last + 1):
            try:
                week = get(f"https://api.sleeper.com/stats/nfl/{SEASON}/{w}?{q}").json()
            except Exception:
                continue
            for e in week:
                a = agg.setdefault(e["player_id"], {"player": e.get("player"), "team": e.get("team"), "stats": {}})
                a["team"] = e.get("team") or a.get("team")
                for f in SLEEPER_FIELDS:
                    a["stats"][f] = (a["stats"].get(f) or 0) + ((e.get("stats") or {}).get(f) or 0)
        how = f"sum of weeks 1-{last}"
    rows = []
    for pid, e in agg.items():
        p, s = e.get("player") or {}, e.get("stats") or {}
        pos = p.get("position")
        if pos not in POS or not s.get("gp"):
            continue
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        rows.append([pid, name, pos, e.get("team") or p.get("team")] + [s.get(f) or 0 for f in SLEEPER_FIELDS])
    rows.sort(key=lambda r: -(r[5] or 0))
    return doc(f"Sleeper season stats {SEASON} (QB/RB/WR/TE, gp>=1, sorted by pts_ppr; {how})", TODAY,
               "sleeperId|name|pos|team|gp|pts_ppr|off_snp|tm_off_snp|rec_tgt|rush_att", rows)


# --------------------------------------------------------------------- ESPN
ESPN = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}"
ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}


def espn_ros():
    season = get(f"{ESPN}?view=proTeamSchedules_wl").json()
    week = (season.get("currentScoringPeriod") or {}).get("id") or 1
    teams = {t["id"]: t for t in (season.get("settings") or {}).get("proTeams", [])}
    flt = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
                       "filterSlotIds": {"value": [0, 2, 4, 6, 23]},
                       "limit": 700, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    data = get(f"{ESPN}/segments/0/leaguedefaults/3?view=kona_player_info&scoringPeriodId={week}",
               headers={"x-fantasy-filter": json.dumps(flt)}).json()
    ros_id, week_id, act_id = f"10{SEASON}", f"11{SEASON}{week}", f"00{SEASON}"
    rows = []
    for e in data.get("players", []):
        p = e.get("player") or e
        pos = ESPN_POS.get(p.get("defaultPositionId"))
        if not pos:
            continue
        st = {s.get("id"): s.get("appliedTotal") for s in p.get("stats", [])}
        ros = st.get(ros_id)
        if not ros:
            continue
        team = teams.get(p.get("proTeamId"), {})
        rows.append([p.get("fullName"), pos, (team.get("abbrev") or "FA").upper(),
                     round(ros, 1), round(st.get(week_id) or 0, 1), round(st.get(act_id) or 0, 1),
                     p.get("injuryStatus") or "", team.get("byeWeek") or ""])
    rows.sort(key=lambda r: -r[3])
    return doc(f"ESPN Fantasy kona_player_info leaguedefaults (scoringPeriod {week}, stat {ros_id} ROS)", TODAY,
               "name|pos|team|ros|weekProj|actual|injuryStatus|byeWeek", rows, week=week)


# --------------------------------------------------------- FantasyPros flex
def fp_flex():
    html = get("https://www.fantasypros.com/nfl/rankings/ros-ppr-flex.php").text
    m = re.search(r"var\s+ecrData\s*=\s*(\{.*?\});", html, re.S)
    if not m:
        raise RuntimeError("ecrData not found on the FantasyPros page (layout changed?)")
    ecr = json.loads(m.group(1))
    rows = [[p.get("player_name"), p.get("player_position_id"), p.get("player_team_id"),
             p.get("rank_ecr"), p.get("pos_rank")] for p in ecr.get("players", [])
            if p.get("player_position_id") in POS]
    rows.sort(key=lambda r: r[3] or 9999)
    return doc("FantasyPros ROS PPR flex consensus (ros-ppr-flex.php)",
               ecr.get("last_updated") or TODAY, "name|pos|team|rank_ecr|pos_rank", rows)


SOURCES = {
    "raw_fc_dynasty": lambda: fantasycalc(True),
    "raw_fc_redraft": lambda: fantasycalc(False),
    "raw_ktc": ktc,
    "raw_sleeper_season": sleeper_season,
    "raw_espn_ros": espn_ros,
    "raw_fp_flex": fp_flex,
}


def main():
    DATA.mkdir(exist_ok=True)
    status = {"runAt": NOW_ISO, "sources": {}}
    for name, fn in SOURCES.items():
        path = DATA / f"{name}.json"
        try:
            d = fn()
            n, floor = d["count"], MINIMUM[name]
            if n < floor * 0.5:
                raise RuntimeError(f"only {n} rows (minimum {floor}); kept the previous file")
            path.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
            status["sources"][name] = {"ok": True, "count": n, "short": n < floor, "minimum": floor}
            print(f"{name}: {n} rows")
        except Exception as exc:
            prev = None
            if path.exists():
                prev = json.loads(path.read_text()).get("asOf")
            status["sources"][name] = {"ok": False, "error": str(exc)[:300], "previousAsOf": prev}
            print(f"{name}: FAILED {exc}", file=sys.stderr)
            traceback.print_exc()
    (DATA / "status.json").write_text(json.dumps(status, indent=1) + "\n")
    ok = sum(1 for s in status["sources"].values() if s["ok"])
    print(f"{ok}/{len(SOURCES)} sources saved")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
