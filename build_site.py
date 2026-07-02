#!/usr/bin/env python3
# ============================================================
# build_site.py — daily MLB matchup site builder (CI port of
# notebooks/Shrunk_mlb_matchup_render_consolidated.ipynb)
#
# Pipeline (render-free, direct API pulls — no browser):
#   FETCH   : MLB StatsAPI (slate, probables, rosters, bio, vL/vR splits)
#             + Savant gf?game_pk= (posted lineups, roster top-PA fallback)
#             + Savant CSV leaderboards (custom + batted-ball, cached per day)
#   MATCHUP : log5 / odds-ratio pitcher-vs-lineup Statcast matchup
#             (M = B*P/L; EV/LA additive), edge = M - L, plus the
#             reliability-shrunk composite score (comp_z)
#   PLATOON : OPS-vs-hand matchup, both sides regressed toward
#             overall x league-platoon prior, reliability-gated
#   OUTPUT  : public/index.html          consolidated per-game cards
#             data/leans_{DATE}_xw.csv   model dump for grade_leans.py
#             data/leans_{DATE}_pl.csv   platoon dump (when available)
#
# Companion: grade_leans.py ingests the data/leans_* dumps into the
# W/L ledger. See MATCHUP_SITE.md for the full CI wiring.
#
# Env overrides: SLATE_DATE (YYYY-MM-DD, default: today US/Eastern),
#                DATA_DIR, SITE_DIR, CACHE_DIR.
# ============================================================
import io
import os
import re
import time
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------
SLATE_DATE = os.environ.get("SLATE_DATE") or \
    datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
SPORT_ID      = 1
SEASON        = int(SLATE_DATE[:4])
LINEUP_SIZE   = 9
REQUEST_DELAY = 0.25
DATA_DIR  = os.environ.get("DATA_DIR", "data")
SITE_DIR  = os.environ.get("SITE_DIR", "public")
CACHE_DIR = os.environ.get("CACHE_DIR", ".savant_cache")
SITE_PATH = os.path.join(SITE_DIR, "index.html")

STATCAST_SELECTIONS = ["pa", "k_percent", "bb_percent", "xwoba", "xba", "xslg",
                       "exit_velocity_avg", "launch_angle_avg", "hard_hit_percent"]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/csv,*/*",
})

# ------------------------------------------------------------
# HTTP + CACHE HELPERS
# ------------------------------------------------------------
def _get_json(url, params=None, tries=3):
    for k in range(tries):
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.6 * (k + 1))

def cached_csv(url, cache_name):
    """Fetch a Savant CSV leaderboard, caching the raw text once per day."""
    path = os.path.join(CACHE_DIR, f"savant_cache_{cache_name}_{SLATE_DATE}.csv")
    if os.path.exists(path):
        return pd.read_csv(path)
    r = session.get(url, timeout=60); r.raise_for_status()
    try:
        with open(path, "w") as f:
            f.write(r.text)
    except Exception:
        pass
    return pd.read_csv(io.StringIO(r.text))

# ------------------------------------------------------------
# 1. SLATE (StatsAPI)
# ------------------------------------------------------------
def get_slate(slate_date, sport_id=1):
    data = _get_json("https://statsapi.mlb.com/api/v1/schedule",
                     {"sportId": sport_id, "date": slate_date,
                      "hydrate": "probablePitcher,team,venue,linescore"})
    rows = []
    for db in data.get("dates", []):
        od = db.get("date", slate_date)
        for g in db.get("games", []):
            a, h = g["teams"]["away"], g["teams"]["home"]
            rows.append({
                "game_pk": g.get("gamePk"),
                "game_date": od,
                "game_datetime_utc": g.get("gameDate"),
                "matchup": f'{a["team"]["name"]} @ {h["team"]["name"]}',
                "away_team": a["team"]["name"], "home_team": h["team"]["name"],
                "away_team_id": a["team"]["id"], "home_team_id": h["team"]["id"],
                "away_abbrev": a["team"].get("abbreviation"),
                "home_abbrev": h["team"].get("abbreviation"),
                "away_probable_pitcher": a.get("probablePitcher", {}).get("fullName"),
                "home_probable_pitcher": h.get("probablePitcher", {}).get("fullName"),
                "away_probable_pitcher_id": a.get("probablePitcher", {}).get("id"),
                "home_probable_pitcher_id": h.get("probablePitcher", {}).get("id"),
                "status": g.get("status", {}).get("detailedState"),
                "venue": g.get("venue", {}).get("name"),
                "savant_preview_url": f'https://baseballsavant.mlb.com/preview?game_pk={g.get("gamePk")}&game_date={od}',
            })
    return pd.DataFrame(rows)

# ------------------------------------------------------------
# 2. STAT LOOKUPS (Savant leaderboards, cached)
# ------------------------------------------------------------
def load_stat_lookups(player_type):
    """player_type in {'batter','pitcher'} -> dict[player_id] = stat dict."""
    sel = ",".join(STATCAST_SELECTIONS)
    cust = cached_csv(
        f"https://baseballsavant.mlb.com/leaderboard/custom?year={SEASON}"
        f"&type={player_type}&min=1&selections={sel}&csv=true",
        f"custom_{player_type}")
    bb = cached_csv(
        f"https://baseballsavant.mlb.com/leaderboard/batted-ball?type={player_type}"
        f"&year={SEASON}&min=1&csv=true",
        f"battedball_{player_type}")

    REN_STAT = {"xwoba": "xwOBA", "xba": "xBA", "xslg": "xSLG", "exit_velocity_avg": "EV",
                "launch_angle_avg": "LA°", "hard_hit_percent": "Hard Hit%",
                "k_percent": "K%", "bb_percent": "BB%", "pa": "PA"}
    stat = {}
    for _, r in cust.iterrows():
        pid = int(r["player_id"])
        stat[pid] = {REN_STAT[k]: r.get(k) for k in REN_STAT if k in cust.columns}

    # batted-ball: rates are 0-1 -> x100; also carries BBE
    BB_REN = {"gb_rate": "GB%", "fb_rate": "FB%", "ld_rate": "LD%", "pu_rate": "PU%",
              "pull_rate": "Pull%", "straight_rate": "Straight%", "oppo_rate": "Oppo%"}
    bbprofile = {}
    for _, r in bb.iterrows():
        pid = int(r["id"])
        prof = {v: (r[k] * 100 if pd.notna(r[k]) else np.nan) for k, v in BB_REN.items() if k in bb.columns}
        bbprofile[pid] = prof
        stat.setdefault(pid, {})
        stat[pid]["BBE"] = r.get("bbe")
    return stat, bbprofile, cust

# ------------------------------------------------------------
# 3. PLAYER BIO (names, positions) — batched StatsAPI
# ------------------------------------------------------------
def load_people(ids):
    ids = [int(i) for i in ids if pd.notna(i)]
    info = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        data = _get_json("https://statsapi.mlb.com/api/v1/people",
                         {"personIds": ",".join(map(str, chunk))})
        for p in data.get("people", []):
            info[p["id"]] = {
                "name": p.get("fullName"),
                "pos": (p.get("primaryPosition", {}) or {}).get("abbreviation"),
                "bats": (p.get("batSide", {}) or {}).get("code"),
                "throws": (p.get("pitchHand", {}) or {}).get("code"),
            }
    return info

def _parse_rate(x):
    try:
        return float(str(x))            # ".720" -> 0.72, "1.045" -> 1.045
    except Exception:
        return np.nan

def load_splits(ids, group):
    """vL/vR splits + season overall via batched hydrate. group in {'hitting','pitching'}.
       Returns dict[player_id] = {'L':{...}, 'R':{...}, 'overall':{ops,pa}}."""
    ids = [int(i) for i in ids if pd.notna(i)]
    pa_field = "plateAppearances" if group == "hitting" else "battersFaced"
    out = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        hyd = f"stats(group=[{group}],type=[statSplits,season],sitCodes=[vl,vr],season={SEASON})"
        data = _get_json("https://statsapi.mlb.com/api/v1/people",
                         {"personIds": ",".join(map(str, chunk)), "hydrate": hyd})
        for p in data.get("people", []):
            rec = {"L": {}, "R": {}, "overall": {}}
            for blk in p.get("stats", []):
                tname = (blk.get("type", {}) or {}).get("displayName")
                for sk in blk.get("splits", []):
                    code = sk.get("split", {}).get("code")     # 'vl' / 'vr' / None(season)
                    st = sk.get("stat", {})
                    val = {"ops": _parse_rate(st.get("ops")),
                           "obp": _parse_rate(st.get("obp")),
                           "slg": _parse_rate(st.get("slg")),
                           "pa":  int(st.get(pa_field) or st.get("plateAppearances") or 0),
                           "k":   st.get("strikeOuts"), "bb": st.get("baseOnBalls")}
                    if tname == "statSplits" and code in ("vl", "vr"):
                        rec["L" if code == "vl" else "R"] = val
                    elif tname == "season" and code is None:
                        rec["overall"] = {"ops": val["ops"], "pa": val["pa"]}
            out[p["id"]] = rec
        time.sleep(REQUEST_DELAY)
    return out

# ------------------------------------------------------------
# 4. LINEUPS — gf JSON primary, roster top-PA fallback
# ------------------------------------------------------------
def gf_lineups(game_pk):
    try:
        gf = _get_json(f"https://baseballsavant.mlb.com/gf?game_pk={game_pk}")
        return [int(x) for x in gf.get("away_lineup", []) or []], \
               [int(x) for x in gf.get("home_lineup", []) or []]
    except Exception:
        return [], []

_roster_cache = {}
def roster_lineup(team_id, batter_stat):
    """Projected lineup = active-roster position players, top LINEUP_SIZE by PA."""
    if team_id in _roster_cache:
        ids = _roster_cache[team_id]
    else:
        data = _get_json(f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster",
                         {"rosterType": "active"})
        ids = [r["person"]["id"] for r in data.get("roster", [])
               if (r.get("position", {}) or {}).get("abbreviation") != "P"]
        _roster_cache[team_id] = ids
    ranked = sorted([i for i in ids if i in batter_stat],
                    key=lambda i: (batter_stat[i].get("PA") or 0), reverse=True)
    return ranked[:LINEUP_SIZE]

def resolve_lineup(game_pk, side, team_id, batter_stat):
    away_ids, home_ids = gf_lineups(game_pk)
    raw = away_ids if side == "away" else home_ids
    valid = [i for i in raw if i in batter_stat][:LINEUP_SIZE]   # drop pitcher id / unknowns
    if len(valid) >= LINEUP_SIZE:
        return valid, False
    return roster_lineup(team_id, batter_stat), True            # projected

# ------------------------------------------------------------
# 5. ASSEMBLE pitchers_df (Savant block layout)
# ------------------------------------------------------------
STAT_COLS = ["BBE", "LA°", "EV", "Hard Hit%", "xwOBA", "xBA", "xSLG", "K%", "BB%"]

def _meta(row):
    return {k: row[k] for k in ["game_pk", "game_date", "matchup", "away_team", "home_team",
                                "away_probable_pitcher", "home_probable_pitcher", "savant_preview_url"]}

def build_tables(slate, lineups, batter_stat, pitcher_stat, people):
    """lineups: dict game_pk -> (away_lineup_ids, home_lineup_ids), pre-resolved.
       Emits the Savant block layout: pitcher row (Pos.=P) then the opposing lineup,
       with exact pitcher->lineup linkage from the resolved lineups."""
    pit_rows = []

    for _, g in slate.iterrows():
        meta = _meta(g)
        asp, hsp = g["away_probable_pitcher_id"], g["home_probable_pitcher_id"]
        away_lu, home_lu = lineups[g["game_pk"]]      # away SP faces home_lu; home SP faces away_lu

        def pitcher_row(pid, tidx):
            if pd.isna(pid):
                return
            pid = int(pid)
            bio = people.get(pid, {})
            nm = bio.get("name") or f"pitcher_{pid}"
            src = pitcher_stat.get(pid, {})
            pit_rows.append({**meta, "table_index": tidx, "Name": nm,
                             "table_type": "pitchers", "Pos.": "P",
                             **{c: src.get(c) for c in STAT_COLS},
                             "player_id": pid, "bats": bio.get("bats"),
                             "throws": bio.get("throws")})

        def hitter_rows(lu, tidx):
            for pid in lu:
                pid = int(pid)
                bio = people.get(pid, {})
                nm = bio.get("name") or f"batter_{pid}"
                pos = bio.get("pos") or "DH"
                if pos == "P":
                    pos = "DH"            # never let a batter look like a divider
                src = batter_stat.get(pid, {})
                pit_rows.append({**meta, "table_index": tidx, "Name": nm,
                                 "table_type": "pitchers", "Pos.": pos,
                                 **{c: src.get(c) for c in STAT_COLS},
                                 "player_id": pid, "bats": bio.get("bats"),
                                 "throws": bio.get("throws")})

        # block 1: away SP + home lineup ; block 2: home SP + away lineup
        pitcher_row(asp, 1); hitter_rows(home_lu, 1)
        pitcher_row(hsp, 2); hitter_rows(away_lu, 2)

    META = ["game_pk", "game_date", "matchup", "away_team", "home_team",
            "away_probable_pitcher", "home_probable_pitcher", "savant_preview_url",
            "table_type", "table_index", "Name"]
    pdf = pd.DataFrame(pit_rows)
    if not pdf.empty:
        pdf = pdf[META + ["Pos."] + STAT_COLS + ["player_id", "bats", "throws"]]
    return pdf

# ============================================================
# MATCHUP — log5 / odds-ratio pitcher vs opponent lineup
# ============================================================
STATCAST_RATE_COLS = ["xwOBA", "xBA", "xSLG", "Hard Hit%", "EV", "LA°", "K%", "BB%"]
WEIGHT_COL         = "BBE"          # sample-size weight for lineup aggregation
USE_WEIGHTED       = True           # headline opp value = BBE-weighted mean (else simple mean)

# Matchup is combined via the log5 / odds-ratio method, anchored on league average L:
#   rate/probability stats -> multiplicative  M = B*P/L
#   continuous measurements -> additive        M = B + P - L
# The edge vs league (M - L) is the signal; a suppressing pitcher (P<L) correctly pulls it
# toward pitcher-favorable. (Plain B-P is NOT used: it carries the pitcher term backwards.)
MULT_STATS = {"xwOBA", "xBA", "xSLG", "Hard Hit%", "K%", "BB%"}
ADD_STATS  = {"EV", "LA°"}
# Direction: higher matchup value than league favors hitters, EXCEPT K% (higher = pitcher).
HITTER_FAVORABLE_UP   = {"xwOBA", "xBA", "xSLG", "Hard Hit%", "EV", "BB%"}
HITTER_FAVORABLE_DOWN = {"K%"}

def matchup_value(B, P, stat, L):
    """Odds-ratio (mult) or additive (cont) expected matchup level. NaN-safe."""
    if pd.isna(B) or pd.isna(P) or pd.isna(L):
        return np.nan
    if stat in ADD_STATS:
        return B + P - L
    return (B * P / L) if L else np.nan

def is_hitter_favorable(edge, stat):
    if pd.isna(edge):
        return False
    if stat in HITTER_FAVORABLE_DOWN:
        return edge < 0
    return edge > 0

# ------------------------------------------------------------
# COMPOSITE MATCHUP SCORE — config + helpers
#   Rolls the per-metric league-anchored edges into ONE standardized score. Each
#   edge is converted to sigma units against a reference spread, weighted
#   (xwOBA-centric), then shrunk toward 0 by sample reliability so a thin SP /
#   thin lineup can't manufacture a big score.
# ------------------------------------------------------------
# metric -> (weight, direction sign): +1 higher=offense-favorable, -1 higher=pitcher
COMPOSITE_WEIGHTS = {
    "xwOBA":     (0.40, +1),
    "xSLG":      (0.20, +1),
    "Hard Hit%": (0.15, +1),
    "EV":        (0.10, +1),
    "K%":        (0.15, -1),
}
K_REL_BBE = 60      # pitcher-BBE shrink: reliability = BBE/(BBE+K) toward league (0)
MIN_OPP_FULL = 6    # opposing hitters linked for full lineup credit (fewer -> shrink)
# fallback per-metric edge SD if the league populations aren't in scope
COMPOSITE_EDGE_SD_FALLBACK = {
    "xwOBA": 0.030, "xSLG": 0.060, "Hard Hit%": 6.0, "EV": 1.5, "K%": 4.5,
}
COMPOSITE_TIERS = [(1.3, "strong"), (0.8, "notable"), (0.4, "slight"), (0.0, "negligible")]

def _wstd(vals, wts=None):
    """Weighted population std (ddof=0), NaN-safe."""
    v = pd.to_numeric(pd.Series(vals).reset_index(drop=True), errors="coerce")
    if wts is None:
        v = v.dropna()
        return float(v.std(ddof=0)) if len(v) > 1 else np.nan
    w = pd.to_numeric(pd.Series(wts).reset_index(drop=True), errors="coerce")
    m = v.notna() & w.notna() & (w > 0)
    if int(m.sum()) < 2:
        return np.nan
    vv, ww = v[m].to_numpy(), w[m].to_numpy()
    mu = np.average(vv, weights=ww)
    return float(np.sqrt(np.average((vv - mu) ** 2, weights=ww)))

def build_edge_sd_reference(batter_cust, pitcher_stat):
    """Per-metric reference SD of the league-anchored edge, from the loaded batter +
       pitcher populations. First-order near league avg, both the multiplicative
       (M=B*P/L) and additive (M=B+P-L) forms give edge ~= (B-L)+(P-L), so
       sigma_edge ~= sqrt(sigma_lineupB^2 + sigma_P^2). Lineup B is a ~9-bat mean, so
       its dispersion is per-batter sigma / 3. Falls back to constants if pops absent."""
    ref = dict(COMPOSITE_EDGE_SD_FALLBACK)
    b_raw = {"xwOBA": "xwoba", "xSLG": "xslg", "Hard Hit%": "hard_hit_percent",
             "EV": "exit_velocity_avg", "K%": "k_percent"}
    sigB = {}
    try:
        bc = batter_cust
        w = pd.to_numeric(bc.get("pa"), errors="coerce")
        for disp, raw in b_raw.items():
            if raw in bc.columns:
                s = _wstd(pd.to_numeric(bc[raw], errors="coerce"), w)
                if pd.notna(s):
                    sigB[disp] = s / 3.0     # ~9-bat lineup mean
    except Exception:
        pass
    sigP = {}
    try:
        rows = list(pitcher_stat.values())
        for disp in b_raw:
            vals = [r.get(disp) for r in rows]
            wts  = [r.get("BBE") for r in rows]
            s = _wstd(vals, wts if any(pd.notna(x) for x in wts) else None)
            if pd.notna(s):
                sigP[disp] = s
    except Exception:
        pass
    for disp in ref:
        sb, sp = sigB.get(disp), sigP.get(disp)
        if sb is not None or sp is not None:
            combined = float(np.sqrt((sb or 0.0) ** 2 + (sp or 0.0) ** 2))
            if combined > 0:
                ref[disp] = combined
    return ref

def _comp_tier(z):
    if pd.isna(z):
        return ""
    az = abs(z)
    for thr, name in COMPOSITE_TIERS:
        if az >= thr:
            return name
    return "negligible"

def add_composite_score(mdf, pitcher_rows_df, batter_cust, pitcher_stat):
    """Add comp_raw / comp_reliability / comp_z / comp_pctile / comp_tier to mdf."""
    if mdf is None or mdf.empty:
        return mdf
    mdf = mdf.copy()
    edge_sd = build_edge_sd_reference(batter_cust, pitcher_stat)
    # pitcher BBE per (game_pk, pitcher) for the reliability shrink
    bbe_map = {}
    if pitcher_rows_df is not None and not pitcher_rows_df.empty and "BBE" in pitcher_rows_df.columns:
        for _, pr in pitcher_rows_df.iterrows():
            bbe_map[(pr["game_pk"], pr["Name"])] = pd.to_numeric(pr.get("BBE"), errors="coerce")
    comp_raw, comp_z, rel = [], [], []
    for _, r in mdf.iterrows():
        num = wsum = 0.0
        for m, (wt, sgn) in COMPOSITE_WEIGHTS.items():
            e = r.get(f"edge_{m}"); sd = edge_sd.get(m)
            if pd.isna(e) or not sd:
                continue
            num += wt * sgn * (float(e) / sd)
            wsum += wt
        craw = (num / wsum) if wsum > 0 else np.nan     # weighted-mean z (renormalized)
        bbe = bbe_map.get((r.get("game_pk"), r.get("pitcher")), np.nan)
        r_p = float(bbe) / (float(bbe) + K_REL_BBE) if pd.notna(bbe) else 0.5
        n_opp = float(r.get("n_opp") or 0)
        r_b = min(1.0, n_opp / MIN_OPP_FULL) if n_opp > 0 else 0.0
        rr = r_p * r_b
        comp_raw.append(round(craw, 3) if pd.notna(craw) else np.nan)
        comp_z.append(round(craw * rr, 3) if pd.notna(craw) else np.nan)
        rel.append(round(rr, 3))
    mdf["comp_raw"] = comp_raw
    mdf["comp_reliability"] = rel
    mdf["comp_z"] = comp_z
    az = mdf["comp_z"].abs()
    mdf["comp_pctile"] = (az.rank(pct=True) * 100).round(0)
    mdf["comp_tier"] = mdf["comp_z"].map(_comp_tier)
    return mdf

# ------------------------------------------------------------
# MATCHUP HELPERS
# ------------------------------------------------------------
def norm_name(x):
    """Accent/suffix/punage-insensitive name key for matching Savant <-> StatsAPI names."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode()
    s = s.lower().replace("\xa0", " ")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def coerce_numeric(df, cols):
    df = df.copy()
    for c in set(cols) | {WEIGHT_COL}:
        if c in df.columns:
            s = (df[c].astype(str)
                       .str.replace("%", "", regex=False)
                       .str.replace(",", "", regex=False)
                       .str.strip())
            s = s.where(~s.isin(["", "nan", "None", "--", "—"]), other=np.nan)
            df[c] = pd.to_numeric(s, errors="coerce")
    return df

def wmean(vals, wts):
    vals = pd.to_numeric(pd.Series(vals).reset_index(drop=True), errors="coerce")
    if wts is None:                       # weight column absent
        return float(vals.mean(skipna=True)) if vals.notna().any() else np.nan
    wts = pd.to_numeric(pd.Series(wts).reset_index(drop=True), errors="coerce")
    m = vals.notna() & wts.notna() & (wts > 0)
    if not m.any():
        return float(vals.mean(skipna=True)) if vals.notna().any() else np.nan
    return float(np.average(vals[m], weights=wts[m]))

def segment_pitcher_blocks(df, rate_cols):
    """
    Walk each (game_pk, table_index) block IN ORDER. A pitcher-divider row starts a
    new block; the hitter rows that follow are the opposing lineup that pitcher faces.
    Pitcher rows are detected by Pos.=='P' when that column exists, else by the row
    name matching a probable pitcher.
    """
    df = coerce_numeric(df, rate_cols)
    has_pos = "Pos." in df.columns
    pos = df["Pos."].astype(str).str.upper().str.strip() if has_pos else None

    pitcher_rows, hitter_rows = [], []
    for _, g in df.groupby(["game_pk", "table_index"], sort=False):
        cur_p = cur_side = None
        away_key = norm_name(g["away_probable_pitcher"].iloc[0]) if "away_probable_pitcher" in g else ""
        home_key = norm_name(g["home_probable_pitcher"].iloc[0]) if "home_probable_pitcher" in g else ""
        for idx, r in g.iterrows():
            name = r.get("Name")
            nkey = norm_name(name)
            is_pitcher = (pos.loc[idx] == "P") if has_pos else (nkey in {away_key, home_key} and nkey != "")
            if is_pitcher:
                cur_p = name
                cur_side = "away" if nkey == away_key else "home" if nkey == home_key else None
                pitcher_rows.append({**r.to_dict(), "pitcher_side": cur_side})
                continue
            if cur_p is None:
                continue  # stray hitter before any pitcher divider
            bat_side = {"away": "home", "home": "away"}.get(cur_side)
            hitter_rows.append({**r.to_dict(), "faced_pitcher": cur_p,
                                "pitcher_side": cur_side, "batting_side": bat_side})

    P = pd.DataFrame(pitcher_rows)
    H = pd.DataFrame(hitter_rows)
    if not P.empty:
        P = P.drop_duplicates(subset=["game_pk", "Name"]).reset_index(drop=True)
    if not H.empty:
        H = H.drop_duplicates(subset=["game_pk", "faced_pitcher", "Name"]).reset_index(drop=True)
    return P, H

def aggregate_lineup(H, rate_cols, weighted=True):
    if H is None or H.empty:
        return pd.DataFrame()
    out = []
    for (gpk, fp), g in H.groupby(["game_pk", "faced_pitcher"], sort=False):
        rec = {"game_pk": gpk, "faced_pitcher": fp,
               "pitcher_side": g["pitcher_side"].iloc[0],
               "batting_side": g["batting_side"].iloc[0],
               "n_opp_hitters": len(g)}
        for c in rate_cols:
            if c not in g.columns:
                continue
            rec[f"opp_{c}_mean"]  = round(float(g[c].mean(skipna=True)), 3) if g[c].notna().any() else np.nan
            rec[f"opp_{c}_wmean"] = round(wmean(g[c], g.get(WEIGHT_COL)), 3)
            rec[f"opp_{c}"]       = rec[f"opp_{c}_wmean"] if weighted else rec[f"opp_{c}_mean"]
        out.append(rec)
    return pd.DataFrame(out)

def build_matchup(P, agg, rate_cols, league_baseline):
    if P.empty or agg.empty:
        return pd.DataFrame()
    Pk = P.set_index(["game_pk", "Name"])
    rows = []
    for _, a in agg.iterrows():
        key = (a["game_pk"], a["faced_pitcher"])
        if key not in Pk.index:
            continue
        pr = Pk.loc[key]
        if isinstance(pr, pd.DataFrame):
            pr = pr.iloc[0]
        side = a["pitcher_side"]
        opp_team = pr.get("home_team") if side == "away" else pr.get("away_team") if side == "home" else None
        rec = {"game_pk": a["game_pk"], "game_date": pr.get("game_date"),
               "matchup": pr.get("matchup"), "side": side, "pitcher": a["faced_pitcher"],
               "opp_team": opp_team, "n_opp": int(a["n_opp_hitters"])}
        for c in rate_cols:
            pv = pd.to_numeric(pr.get(c), errors="coerce")          # P (pitcher allowed)
            ov = a.get(f"opp_{c}")                                   # B (opponent lineup)
            L  = league_baseline.get(c, np.nan)                      # league anchor
            rec[f"pit_{c}"] = round(float(pv), 3) if pd.notna(pv) else np.nan
            rec[f"opp_{c}"] = ov
            rec[f"lg_{c}"]  = L
            M = matchup_value(float(pv) if pd.notna(pv) else np.nan,
                              float(ov) if pd.notna(ov) else np.nan, c, L)
            rec[f"mx_{c}"]   = round(M, 3) if pd.notna(M) else np.nan          # expected matchup level
            rec[f"edge_{c}"] = round(M - L, 3) if pd.notna(M) and pd.notna(L) else np.nan  # vs league
        rows.append(rec)
    df = pd.DataFrame(rows)
    return df.sort_values(["game_pk", "side"]).reset_index(drop=True)

# ============================================================
# PLATOON / HANDEDNESS SPLIT MATCHUP (OPS vs hand, regressed)
# ============================================================
MIN_SPLIT_PA = 30          # batter vs-hand split below this PA = flagged low-sample
MIN_PITCHER_SPLIT_BF = 50  # pitcher vs-stand split below this BF = prior-driven (flag line)
# Platoon splits stabilize slowly, so vs-hand rates are regressed toward a prior:
#   prior = player's OVERALL rate * (league_cell / league_overall)
#   est   = (n*observed + K*prior) / (n + K)
K_BAT = 100
K_PIT = 200
K0 = 200                   # regress a player's OVERALL toward league
OPP_HAND = {"L": "R", "R": "L"}

def _shrink(obs, n, overall, overall_pa, Lcell, Loverall, K):
    """Two-stage: (1) regress player's overall toward league by its own PA, (2) apply league
       platoon multiplier as the prior, (3) regress the observed vs-hand split toward it."""
    if overall is not None and not pd.isna(overall) and Loverall:
        op = overall_pa or 0
        overall_reg = (op * overall + K0 * Loverall) / (op + K0)   # stage 1
        prior = overall_reg * (Lcell / Loverall) if Lcell else overall_reg  # stage 2
    else:
        prior = Lcell if Lcell else Loverall
    if obs is None or pd.isna(obs):
        return prior
    n = n or 0
    return (n * obs + K * prior) / (n + K) if (n + K) > 0 else prior   # stage 3

def _wmean_req(v, w):
    v = pd.to_numeric(pd.Series(v).reset_index(drop=True), errors="coerce")
    w = pd.to_numeric(pd.Series(w).reset_index(drop=True), errors="coerce")
    m = v.notna() & w.notna() & (w > 0)
    return float(np.average(v[m], weights=w[m])) if m.any() else np.nan

def build_platoon(pitcher_rows_df, opp_hitters_df, player_splits_hit, player_splits_pit, people):
    """Returns (matchup_platoon_df, opp_platoon_detail_df)."""
    if pitcher_rows_df is None or pitcher_rows_df.empty or \
       opp_hitters_df is None or opp_hitters_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    _pmeta = pitcher_rows_df.set_index(["game_pk", "Name"])
    def _pitcher_attr(gpk, name, attr):
        try:
            v = _pmeta.loc[(gpk, name), attr]
            return v.iloc[0] if isinstance(v, pd.Series) else v
        except KeyError:
            return None

    def _hit_split(pid, hand):
        return (player_splits_hit.get(int(pid), {}) or {}).get(hand, {}) if pd.notna(pid) else {}
    def _pit_split(pid, hand):
        return (player_splits_pit.get(int(pid), {}) or {}).get(hand, {}) if pd.notna(pid) else {}
    def _overall(splits, pid):
        o = (splits.get(int(pid), {}) or {}).get("overall", {}) or {} if pd.notna(pid) else {}
        return o.get("ops"), (o.get("pa") or 0)
    def _bats_of(pid):
        return (people.get(int(pid), {}) or {}).get("bats") if pd.notna(pid) else None

    # league OPS baselines per platoon cell (batter-stand vs pitcher-throw)
    def _compute_league_ops_cells():
        buckets = {("L", "L"): [], ("L", "R"): [], ("R", "L"): [], ("R", "R"): []}
        allv = []
        for pid, sp in player_splits_hit.items():
            b = (_bats_of(pid) or "")[:1].upper()
            for T in ("L", "R"):
                s = sp.get(T, {}) or {}
                ops, pa = s.get("ops"), s.get("pa") or 0
                if ops is None or (isinstance(ops, float) and np.isnan(ops)) or pa <= 0:
                    continue
                eff = b if b in ("L", "R") else OPP_HAND[T]          # switch bats opposite pitcher
                buckets[(eff, T)].append((ops, pa)); allv.append((ops, pa))
        Lc = {}
        for k, v in buckets.items():
            if v:
                o = np.array([x[0] for x in v]); w = np.array([x[1] for x in v], float)
                Lc[k] = round(float(np.average(o, weights=w)), 3)
        overall = round(float(np.average([x[0] for x in allv],
                       weights=[x[1] for x in allv])), 3) if allv else np.nan
        return Lc, overall

    league_ops_cell, league_ops_overall = _compute_league_ops_cells()
    print("League OPS cells (batter-stand vs pitcher-throw):",
          {f"{s}v{t}": league_ops_cell.get((s, t)) for s in "LR" for t in "LR"},
          "| overall", league_ops_overall)

    rows = []
    for _, h in opp_hitters_df.iterrows():
        gpk, fp = h["game_pk"], h["faced_pitcher"]
        T = _pitcher_attr(gpk, fp, "throws")          # pitcher hand L/R
        if T not in ("L", "R"):
            continue
        bats = (h.get("bats") or "")[:1].upper()
        pid = h.get("player_id")
        pid_p = _pitcher_attr(gpk, fp, "player_id")
        eff_stand = bats if bats in ("L", "R") else OPP_HAND[T]   # switch bats opposite pitcher
        L = league_ops_cell.get((eff_stand, T), league_ops_overall)
        Lo = league_ops_overall
        # raw observed splits + sample sizes
        B_raw = _hit_split(pid, T).get("ops");  pa_b = _hit_split(pid, T).get("pa") or 0
        P_raw = (_pit_split(pid_p, eff_stand) or {}).get("ops")
        bf_p  = (_pit_split(pid_p, eff_stand) or {}).get("pa") or 0
        # regress both toward (league-regressed overall) x league-platoon prior
        ov_b, ov_b_pa = _overall(player_splits_hit, pid)
        ov_p, ov_p_pa = _overall(player_splits_pit, pid_p)
        B = _shrink(B_raw, pa_b, ov_b, ov_b_pa, L, Lo, K_BAT)
        P = _shrink(P_raw, bf_p, ov_p, ov_p_pa, L, Lo, K_PIT)
        Mi = (B * P / L) if (B is not None and P is not None and L) and not (
              pd.isna(B) or pd.isna(P)) else np.nan
        rows.append({
            "game_pk": gpk, "matchup": h.get("matchup"), "faced_pitcher": fp,
            "pitcher_throws": T, "batter": h["Name"], "bats": bats or "?",
            "eff_stand": eff_stand, "platoon_adv": eff_stand != T,
            "ops_vs_hand_raw": B_raw, "ops_vs_hand": round(B, 3) if pd.notna(B) else np.nan,
            "pit_ops_allowed_raw": P_raw, "pit_ops_allowed": round(P, 3) if pd.notna(P) else np.nan,
            "lg_cell": L, "mx_ops": round(Mi, 3) if pd.notna(Mi) else np.nan,
            "split_pa": pa_b, "pit_split_bf": bf_p,
            "low_sample": pa_b < MIN_SPLIT_PA,
            "pit_low_sample": bf_p < MIN_PITCHER_SPLIT_BF,
        })
    opp_platoon_detail_df = pd.DataFrame(rows)
    if opp_platoon_detail_df.empty:
        return pd.DataFrame(), opp_platoon_detail_df

    # aggregate per (game, probable) — odds-ratio matchup, league-anchored edge
    plat_rows = []
    for (gpk, fp), g in opp_platoon_detail_df.groupby(["game_pk", "faced_pitcher"], sort=False):
        T = g["pitcher_throws"].iloc[0]
        opp_ops_raw = _wmean_req(g["ops_vs_hand_raw"], g["split_pa"])      # raw B (context)
        opp_ops = _wmean_req(g["ops_vs_hand"], g["split_pa"])              # shrunk B
        pit_ops_raw = float(np.nanmean(pd.to_numeric(g["pit_ops_allowed_raw"], errors="coerce"))) \
                      if g["pit_ops_allowed_raw"].notna().any() else np.nan
        pit_ops = float(np.nanmean(pd.to_numeric(g["pit_ops_allowed"], errors="coerce"))) \
                  if g["pit_ops_allowed"].notna().any() else np.nan
        mx_ops = _wmean_req(g["mx_ops"], g["split_pa"])                    # PA-weighted shrunk odds-ratio
        edge = mx_ops - league_ops_overall if pd.notna(mx_ops) else np.nan
        # pitcher reliability: thinner of the vs-stand splits the lineup actually exposes
        bf_present = [v for v in g.loc[g["pit_split_bf"] > 0, "pit_split_bf"].unique()]
        pit_min_bf = int(min(bf_present)) if bf_present else 0
        pit_low = bool(g["pit_low_sample"].any())
        meta_row = opp_hitters_df[(opp_hitters_df.game_pk == gpk) &
                                  (opp_hitters_df.faced_pitcher == fp)].iloc[0]
        opp_team = meta_row["home_team"] if meta_row["pitcher_side"] == "away" else meta_row["away_team"]
        plat_rows.append({
            "game_pk": gpk, "game_date": meta_row.get("game_date"), "matchup": meta_row["matchup"],
            "side": meta_row["pitcher_side"], "pitcher": fp, "throws": T, "opp_team": opp_team,
            "n_opp": len(g),
            "n_LHB": int((g["bats"] == "L").sum()), "n_RHB": int((g["bats"] == "R").sum()),
            "n_SW": int((g["bats"] == "S").sum()),
            "n_platoon_adv": int(g["platoon_adv"].sum()),
            "n_low_sample": int(g["low_sample"].sum()),
            "pit_min_split_bf": pit_min_bf, "pit_low_sample": pit_low,
            "reliable": (not pit_low) and (int(g["low_sample"].sum()) <= 4),
            "opp_OPS_raw": round(opp_ops_raw, 3) if pd.notna(opp_ops_raw) else np.nan,
            "opp_OPS_vs_hand": round(opp_ops, 3) if pd.notna(opp_ops) else np.nan,
            "pit_OPS_raw": round(pit_ops_raw, 3) if pd.notna(pit_ops_raw) else np.nan,
            "pit_OPS_allowed": round(pit_ops, 3) if pd.notna(pit_ops) else np.nan,
            "mx_OPS": round(mx_ops, 3) if pd.notna(mx_ops) else np.nan,
            "edge_OPS": round(edge, 3) if pd.notna(edge) else np.nan,
        })
    matchup_platoon_df = (pd.DataFrame(plat_rows)
                          .sort_values(["game_pk", "side"]).reset_index(drop=True))
    return matchup_platoon_df, opp_platoon_detail_df

# ============================================================
# CONSOLIDATED MATCHUP-CARD RENDER
#   ONE card per game, both lenses stacked per probable:
#   - Statcast xwOBA strip (xwOBA / HardHit / EV / xSLG / K%)
#   - composite score line
#   - Platoon OPS matchup (regressed odds-ratio OPS vs hand)
# ============================================================
ABBR = {
 "Arizona Diamondbacks":"ARI","Athletics":"ATH","Atlanta Braves":"ATL","Baltimore Orioles":"BAL",
 "Boston Red Sox":"BOS","Chicago Cubs":"CHC","Chicago White Sox":"CWS","Cincinnati Reds":"CIN",
 "Cleveland Guardians":"CLE","Colorado Rockies":"COL","Detroit Tigers":"DET","Houston Astros":"HOU",
 "Kansas City Royals":"KC","Los Angeles Angels":"LAA","Los Angeles Dodgers":"LAD","Miami Marlins":"MIA",
 "Milwaukee Brewers":"MIL","Minnesota Twins":"MIN","New York Mets":"NYM","New York Yankees":"NYY",
 "Philadelphia Phillies":"PHI","Pittsburgh Pirates":"PIT","San Diego Padres":"SD","San Francisco Giants":"SF",
 "Seattle Mariners":"SEA","St. Louis Cardinals":"STL","Tampa Bay Rays":"TB","Texas Rangers":"TEX",
 "Toronto Blue Jays":"TOR","Washington Nationals":"WSH",
}

def clamp(x, a, b): return a if x < a else b if x > b else x

def tint(edge, domain, ksign=1):
    """rgba bg for a metric chip. warm = offense-favorable, cool = pitcher-favorable.
       alpha scales with |edge|/domain so conviction reads as intensity."""
    if edge is None: return "transparent"
    s = edge * ksign
    t = clamp(abs(s) / domain, 0, 1) ** 0.85
    rgb = "var(--warm)" if s >= 0 else "var(--cool)"
    a = round(0.08 + 0.60 * t, 3)
    return f"rgba({rgb},{a})"

def edge_color(edge, ksign=1):
    if edge is None: return "var(--faint)"
    return "rgba(var(--warm),1)" if edge * ksign >= 0 else "rgba(var(--cool),1)"

def f3(v):  return "—" if v is None else f"{v:.3f}".lstrip("0") if 0 < abs(v) < 1 else f"{v:.3f}"
def f1(v):  return "—" if v is None else f"{v:.1f}"
def sgn3(v): return "—" if v is None else f"{'+' if v >= 0 else '−'}{abs(v):.3f}".rstrip()
def sgn1(v): return "—" if v is None else f"{'+' if v >= 0 else '−'}{abs(v):.1f}"
def sgn2(v): return "—" if v is None else f"{'+' if v >= 0 else '−'}{abs(v):.2f}"

def _comp_line(r):
    """Composite matchup score chip: reliability-shrunk sigma + tier + slate pctile."""
    z = r.get("comp"); tier = r.get("comp_tier") or ""; pct = r.get("comp_pct")
    if z is None:
        return ("<div class='compline'><span class='clab'>composite</span>"
                "<span class='cval muted'>—</span></div>")
    col = edge_color(z); bg = tint(z, 1.3, 1)     # 1.3 sigma (='strong') = full tint
    pctxt = f" · {pct:.0f}ᵗʰ pctile" if pct is not None else ""
    return (f"<div class='compline' style='background:{bg}'>"
            f"<span class='clab'>composite</span>"
            f"<span class='cval' style='color:{col}'>{sgn2(z)}σ</span>"
            f"<span class='ctier'>{tier}{pctxt}</span></div>")

def _abbr(name): return ABBR.get(name, str(name or "")[:3].upper())  # KeyError-safe

def meter(away_abbr, home_abbr, away_off, home_off, domain):
    """Centered diverging lean meter. net>0 tilts toward the home team (right)."""
    if away_off is None or home_off is None:
        return ""
    net = home_off - away_off
    pos = 50 + clamp(net / domain, -1, 1) * 44
    if pos >= 50: fl, fw = 50, pos - 50
    else:         fl, fw = pos, 50 - pos
    fav_home = home_off >= away_off
    lcl = "" if fav_home else " on"
    rcl = " on" if fav_home else ""
    return (
      f"<div class='mx-meter'>"
      f"<span class='pole left{lcl}'>{away_abbr}</span>"
      f"<div class='track'>"
      f"<div class='fill' style='left:{fl:.1f}%;width:{fw:.1f}%'></div>"
      f"<div class='cen'></div>"
      f"<div class='mark' style='left:{pos:.1f}%'></div>"
      f"</div>"
      f"<span class='pole right{rcl}'>{home_abbr}</span>"
      f"</div>")

# ---- xwOBA metric strip ----------------------------------------------------
XW_METRICS = [  # key, label, fmt, domain, ksign  (ksign=-1 -> down is offense-good, i.e. K%)
  ("xw", "xwOBA",  f3, 0.060, 1),
  ("hh", "HardHit", f1, 12.0, 1),
  ("ev", "EV",      f1, 3.0, 1),
  ("xs", "xSLG",    f3, 0.120, 1),
  ("k",  "K%",      f1, 9.0, -1),
]

def _xw_strip(r):
    chips = ""
    for key, lab, fmt, dom, ks in XW_METRICS:
        pit, opp, mx, edge = r[key]
        bg = tint(edge, dom, ks)
        title = f"{lab}  pit {fmt(pit)} / opp {fmt(opp)} -> mx {fmt(mx)}  (edge {sgn3(edge) if dom < 1 else sgn1(edge)})"
        chips += (f"<div class='chip' style='background:{bg}' title='{title}'>"
                  f"<div class='lab'>{lab}</div>"
                  f"<div class='val'>{fmt(mx)}</div>"
                  f"<div class='sub'>{fmt(pit)}<span>/</span>{fmt(opp)}</div></div>")
    return chips

def _pl_chip(r):
    """One wide OPS-matchup chip + flags, reliability-aware. Returns (chip_html, edge, ecol)."""
    if not r.get("has_pl"):
        return ("<div class='chip wide muted'><div class='lab'>OPS matchup</div>"
                "<div class='val'>—</div><div class='sub'>no vs-hand split</div></div>", None, "var(--faint)")
    rel = r["pl_reliable"]; edge = r["pl_edge"]
    bg = tint(edge, 0.20, 1) if rel else "transparent"
    ecol = edge_color(edge) if rel else "var(--faint)"
    flags = ""
    fl = r.get("pl_fl", {})
    if "thin" in fl:  flags += f"<span class='flag warn'>thin SP {fl['thin']}bf</span>"
    if "lowpa" in fl: flags += f"<span class='flag'>{fl['lowpa']} low-PA</span>"
    if not rel:       flags += "<span class='flag mute'>prior-driven</span>"
    chip = (f"<div class='chip wide{'' if rel else ' unrel'}' style='background:{bg}'>"
            f"<div class='lab'>OPS matchup{flags}</div>"
            f"<div class='val'>{f3(r['pl_mx'])}</div>"
            f"<div class='sub'>opp {f3(r['pl_opp_raw'])}<span>/</span>SP {f3(r['pl_sp_raw'])}</div></div>")
    return chip, edge, ecol

def cmb_row(side, r, opp_abbr):
    badge = f" <em>{r['t']}</em>" if r.get('t') in ('L', 'R') else ""
    comp  = f"{r['R']}R/{r['L']}L" + (f"/{r['S']}S" if r['S'] else "") if r.get("has_pl") else "—"
    padv  = f" · {r['padv']} plt-adv" if r.get("has_pl") else ""
    xw_oe = r["xw"][3]                                   # opponent-offense xwOBA edge (drives lean)
    pl_chip, pl_edge, pl_ecol = _pl_chip(r)
    return (
      f"<div class='prow'>"
        f"<div class='pmeta'>"
          f"<div class='pname'>{r['p']}{badge}</div>"
          f"<div class='prole'>{side} SP · vs {opp_abbr} · {comp}{padv}</div>"
        f"</div>"
        # xwOBA lens
        f"<div class='lensline'>"
          f"<div class='strip'>{_xw_strip(r)}</div>"
          f"<div class='offedge' style='color:{edge_color(xw_oe)}' "
          f"title='opponent-offense xwOBA edge vs league — drives the lean'>"
          f"<span>xw edge</span>{sgn3(xw_oe)}</div>"
        f"</div>"
        # composite matchup score
        f"{_comp_line(r)}"
        # platoon OPS lens
        f"<div class='lensline pl'>"
          f"{pl_chip}"
          f"<div class='offedge' style='color:{pl_ecol}' title='opponent-offense platoon-OPS edge vs league'>"
          f"<span>ops edge</span>{sgn3(pl_edge)}</div>"
        f"</div>"
      f"</div>")

def _consensus(away_team, home_team, a, h):
    """xwOBA always present; platoon only when reliable on the favored side. Build the readout."""
    # xwOBA leans
    xw_home, xw_away = a["xw"][3], h["xw"][3]   # away-SP row = home offense; home-SP row = away offense
    xw_fav = home_team if (xw_home is not None and xw_away is not None and xw_home >= xw_away) else away_team
    xw_d   = abs((xw_home or 0) - (xw_away or 0)) if (xw_home is not None and xw_away is not None) else None
    # platoon leans (reliability-aware)
    pl_home = a["pl_edge"] if (a.get("has_pl") and a.get("pl_reliable")) else None
    pl_away = h["pl_edge"] if (h.get("has_pl") and h.get("pl_reliable")) else None
    if pl_home is not None and pl_away is not None:
        pl_fav = home_team if pl_home >= pl_away else away_team
        pl_d   = abs(pl_home - pl_away)
        if pl_fav == xw_fav: tag, tcl = "AGREE", "agree"
        else:                tag, tcl = "DIVERGE", "diverge"
        pl_txt = f"OPS → <b>{pl_fav}</b> Δ{pl_d:.3f}"
    else:
        tag, tcl = "n/a", "na"
        pl_txt = "OPS → <span class='muted'>unreliable / no split</span>"
    xw_txt = (f"xwOBA → <b>{xw_fav}</b> Δ{xw_d:.3f}" if xw_d is not None
              else "xwOBA → <span class='muted'>—</span>")
    cz_home, cz_away = a.get("comp"), h.get("comp")   # away-SP row = home offense
    if cz_home is not None and cz_away is not None:
        c_fav = home_team if cz_home >= cz_away else away_team
        comp_txt = f"comp → <b>{c_fav}</b> Δ{abs(cz_home - cz_away):.2f}σ"
    else:
        comp_txt = "comp → <span class='muted'>—</span>"
    return (f"<div class='consensus'>{xw_txt}<span class='dot'>·</span>{pl_txt}"
            f"<span class='dot'>·</span>{comp_txt}"
            f"<span class='ctag {tcl}'>{tag}</span></div>")

def cmb_card(g):
    a, h = g["away"], g["home"]
    home_team = _abbr(a["opp"])   # away SP faces the home lineup -> its opp IS the home team
    away_team = _abbr(h["opp"])   # home SP faces the away lineup -> its opp IS the away team
    home_off, away_off = a["xw"][3], h["xw"][3]  # xwOBA-driven lean
    ho = home_off if home_off is not None else 0.0
    ao = away_off if away_off is not None else 0.0
    delta = abs(ho - ao)
    fav = home_team if ho >= ao else away_team
    pill_a = round(clamp(delta / 0.06, 0, 1) * 0.16 + 0.04, 3)
    return (
      f"<article class='card'>"
        f"<header class='head'>"
          f"<div class='eyebrow'>{away_team} <span>@</span> {home_team}</div>"
          f"<div class='lean' style='background:rgba(var(--lean),{pill_a})'>"
          f"<span class='lk'>lean</span><span class='lt'>{fav}</span>"
          f"<span class='ld'>Δxw {delta:.3f}</span></div>"
        f"</header>"
        f"{meter(away_team, home_team, away_off, home_off, 0.08)}"
        f"{_consensus(away_team, home_team, a, h)}"
        f"<div class='rows'>{cmb_row('AWAY', a, home_team)}{cmb_row('HOME', h, away_team)}</div>"
      f"</article>")

def build_combined(games):
    cards = sorted(
        games,
        key=lambda g: abs((g['away']['xw'][3] or 0) - (g['home']['xw'][3] or 0)),
        reverse=True)
    return "<div class='grid'>" + "".join(cmb_card(g) for g in cards) + "</div>"

# ------------------------------------------------------------
# DataFrame -> builder-dict adapters
# ------------------------------------------------------------
def _f(v):
    if v is None: return None
    try:
        if pd.isna(v): return None
    except (TypeError, ValueError):
        pass
    try: return float(v)
    except (TypeError, ValueError): return None

def _rows_by_side(gg):
    a = gg[gg["side"] == "away"]; h = gg[gg["side"] == "home"]
    return (a.iloc[0] if len(a) else None), (h.iloc[0] if len(h) else None)

def _tup(r, col):
    return (_f(r.get(f"pit_{col}")), _f(r.get(f"opp_{col}")),
            _f(r.get(f"mx_{col}")),  _f(r.get(f"edge_{col}")))

def _pl_lookup(pl_df):
    """(game_pk, side) -> platoon row dict, for merging into the xwOBA backbone."""
    out = {}
    if pl_df is None or getattr(pl_df, "empty", True):
        return out
    for _, r in pl_df.iterrows():
        out[(r["game_pk"], r["side"])] = r
    return out

def _df_to_combined_games(xw_df, pl_df, throws):
    pl_map = _pl_lookup(pl_df)
    games = []
    for gpk, gg in xw_df.groupby("game_pk", sort=False):
        a, h = _rows_by_side(gg)
        if a is None or h is None:
            continue  # need both probables to draw a paired card
        def mk(r):
            side = r["side"]
            t = throws.get((r["game_pk"], r["pitcher"]), "")
            d = dict(p=r["pitcher"], t=t if t in ("L", "R") else "", opp=r["opp_team"],
                     xw=_tup(r, "xwOBA"), hh=_tup(r, "Hard Hit%"), ev=_tup(r, "EV"),
                     xs=_tup(r, "xSLG"), k=_tup(r, "K%"),
                     comp=_f(r.get("comp_z")), comp_tier=(r.get("comp_tier") or ""),
                     comp_pct=_f(r.get("comp_pctile")),
                     has_pl=False, R=0, L=0, S=0, padv=0, pl_fl={})
            pr = pl_map.get((r["game_pk"], side))
            if pr is not None:
                fl = {}
                if bool(pr.get("pit_low_sample")) and int(pr.get("pit_min_split_bf") or 0) > 0:
                    fl["thin"] = int(pr["pit_min_split_bf"])
                if int(pr.get("n_low_sample") or 0) > 0:
                    fl["lowpa"] = int(pr["n_low_sample"])
                if not d["t"]:
                    pt = pr.get("throws"); d["t"] = pt if pt in ("L", "R") else ""
                d.update(has_pl=True,
                         R=int(pr.get("n_RHB") or 0), L=int(pr.get("n_LHB") or 0),
                         S=int(pr.get("n_SW") or 0), padv=int(pr.get("n_platoon_adv") or 0),
                         pl_opp_raw=_f(pr.get("opp_OPS_raw")), pl_sp_raw=_f(pr.get("pit_OPS_raw")),
                         pl_mx=_f(pr.get("mx_OPS")), pl_edge=_f(pr.get("edge_OPS")),
                         pl_reliable=bool(pr.get("reliable")), pl_fl=fl)
            return d
        games.append(dict(away=mk(a), home=mk(h)))
    return games

def _legend(model_label):
    return (
      "<div class='legend'>"
      f"<div class='lg-title'>{model_label} · {SLATE_DATE} · "
      "<em>offense-vs-starter contact lean, not a win projection</em></div>"
      "<div class='lg-keys'>"
      "<span class='k'><i class='sw warm'></i>offense-favorable</span>"
      "<span class='k'><i class='sw cool'></i>pitcher-favorable</span>"
      "<span class='k'><i class='sw lean'></i>lean / net tilt (xwOBA)</span>"
      "<span class='k'><i class='sw grey'></i>unreliable (prior-driven)</span>"
      "<span class='k'><i class='sw lean'></i>composite σ = reliability-shrunk magnitude</span>"
      "<span class='k note'>top = Statcast xwOBA · mid = composite score · bottom = platoon OPS · cards sorted by xwOBA Δedge</span>"
      "</div></div>")

# ------------------------------------------------------------
# Stylesheet (light + dark via prefers-color-scheme)
# ------------------------------------------------------------
CSS = r"""/* ============================================================
   MLB matchup leans — design tokens
   palette: cool-slate surfaces, semantic diverging warm/cool, indigo lean
   type: system sans for labels, monospace tabular for all figures
   signature: centered diverging lean meter per game
   ============================================================ */
:root{
  --bg:#eef0f4; --surface:#ffffff; --surface-2:#f7f8fb; --ink:#15171c;
  --muted:#646b78; --faint:#9aa1ad; --line:#e6e8ee; --line-2:#eef0f5;
  --warm:206,74,46;        /* offense-favorable (vermilion) */
  --cool:18,138,140;       /* pitcher-favorable (teal)      */
  --lean:88,90,196;        /* net tilt (indigo)             */
  --chip-fg:#1b1e25;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Arial,sans-serif;
  --shadow:0 1px 2px rgba(16,18,29,.05),0 14px 34px -22px rgba(16,18,29,.30);
  --r:14px;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0c0e13; --surface:#161a22; --surface-2:#11141b; --ink:#e9ebf0;
  --muted:#98a0ac; --faint:#646b78; --line:#242a35; --line-2:#1b202a;
  --warm:232,108,76; --cool:52,176,176; --lean:124,126,228; --chip-fg:#e9ebf0;
  --shadow:0 1px 2px rgba(0,0,0,.45),0 18px 40px -24px rgba(0,0,0,.8);
}}
html[data-theme="dark"]{
  --bg:#0c0e13; --surface:#161a22; --surface-2:#11141b; --ink:#e9ebf0;
  --muted:#98a0ac; --faint:#646b78; --line:#242a35; --line-2:#1b202a;
  --warm:232,108,76; --cool:52,176,176; --lean:124,126,228; --chip-fg:#e9ebf0;
  --shadow:0 1px 2px rgba(0,0,0,.45),0 18px 40px -24px rgba(0,0,0,.8);
}

*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  -webkit-font-smoothing:antialiased;padding:22px 16px 60px}
.mx-wrap{max-width:760px;margin:0 auto}

/* topbar / tabs / theme */
.topbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
.tabs{display:inline-flex;background:var(--surface);border:1px solid var(--line);
  border-radius:11px;padding:3px;box-shadow:var(--shadow)}
.tab{appearance:none;border:0;background:transparent;color:var(--muted);font:600 12.5px/1 var(--sans);
  letter-spacing:.02em;padding:8px 13px;border-radius:8px;cursor:pointer;transition:.15s}
.tab:hover{color:var(--ink)}
.tab.on{background:rgba(var(--lean),.12);color:var(--ink)}
.theme{appearance:none;border:1px solid var(--line);background:var(--surface);color:var(--muted);
  font:600 12px/1 var(--sans);padding:8px 11px;border-radius:9px;cursor:pointer}
.theme:hover{color:var(--ink)}

/* legend */
.legend{margin:2px 2px 16px}
.lg-title{font-size:13px;font-weight:650;letter-spacing:.01em;margin-bottom:8px}
.lg-title em{font-style:normal;color:var(--muted);font-weight:500}
.lg-keys{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:11.5px;color:var(--muted)}
.lg-keys .k{display:inline-flex;align-items:center;gap:6px}
.lg-keys .note{color:var(--faint)}
.sw{width:11px;height:11px;border-radius:3px;display:inline-block}
.sw.warm{background:rgba(var(--warm),.85)} .sw.cool{background:rgba(var(--cool),.85)}
.sw.lean{background:rgba(var(--lean),.9)}  .sw.grey{background:var(--line);border:1px solid var(--faint)}

/* grid + card */
.grid{display:flex;flex-direction:column;gap:13px}
.panel.hide{display:none}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);
  box-shadow:var(--shadow);overflow:hidden}

/* header */
.head{display:flex;align-items:center;justify-content:space-between;gap:12px;
  padding:13px 16px 9px}
.eyebrow{font:700 15px/1 var(--mono);letter-spacing:.04em}
.eyebrow span{color:var(--faint);font-weight:500;margin:0 3px}
.lean{display:inline-flex;align-items:baseline;gap:8px;padding:5px 11px;border-radius:999px;
  border:1px solid var(--line-2)}
.lean.soft{opacity:.6}
.lean .lk{font:600 10px/1 var(--sans);text-transform:uppercase;letter-spacing:.13em;color:var(--muted)}
.lean .lt{font:750 15px/1 var(--mono);letter-spacing:.03em;color:var(--ink)}
.lean .ld{font:500 11.5px/1 var(--mono);color:var(--muted)}

/* SIGNATURE — lean meter */
.mx-meter{display:flex;align-items:center;gap:10px;padding:2px 16px 14px}
.mx-meter .pole{font:600 11px/1 var(--mono);letter-spacing:.04em;color:var(--faint);width:34px;
  text-align:center;transition:.15s}
.mx-meter .pole.left{text-align:right} .mx-meter .pole.right{text-align:left}
.mx-meter .pole.on{color:var(--ink)}
.track{position:relative;flex:1;height:7px;background:var(--line-2);border-radius:999px}
.track .cen{position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--faint);opacity:.5}
.track .fill{position:absolute;top:0;bottom:0;background:rgba(var(--lean),.30);border-radius:999px}
.track .mark{position:absolute;top:50%;width:13px;height:13px;border-radius:50%;
  background:rgba(var(--lean),1);transform:translate(-50%,-50%);
  box-shadow:0 0 0 3px var(--surface),0 1px 4px rgba(0,0,0,.28)}

/* pitcher rows */
.rows{border-top:1px solid var(--line-2)}
.prow{padding:11px 16px;display:flex;flex-direction:column;gap:9px}
.prow + .prow{border-top:1px solid var(--line-2)}
.pmeta{display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap}
.pname{font:650 14px/1.1 var(--sans)}
.pname em{font-style:normal;font-family:var(--mono);font-size:11px;color:#fff;
  background:var(--faint);padding:1px 5px;border-radius:5px;margin-left:5px;vertical-align:1px}
.prole{font-size:11.5px;color:var(--muted);letter-spacing:.01em}

/* metric strip */
.strip{display:flex;gap:6px;flex-wrap:wrap}
.chip{flex:1 1 64px;min-width:60px;border:1px solid var(--line-2);border-radius:9px;
  padding:6px 7px 5px;text-align:center;background:transparent}
.chip .lab{font:600 9px/1 var(--sans);text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.chip .val{font:600 16px/1.15 var(--mono);color:var(--chip-fg);font-variant-numeric:tabular-nums;margin-top:2px}
.chip .sub{font:400 10px/1 var(--mono);color:var(--muted);margin-top:2px}
.chip .sub span{color:var(--faint);margin:0 1px}
.chip.wide{flex:0 0 150px;text-align:left}
.chip.wide .val{font-size:18px}

/* offense-edge readout (the lean driver) */
.offedge{font:700 15px/1 var(--mono);font-variant-numeric:tabular-nums;text-align:right;
  display:flex;align-items:baseline;justify-content:flex-end;gap:8px}
.offedge span{font:600 9.5px/1 var(--sans);text-transform:uppercase;letter-spacing:.09em;color:var(--muted)}

/* platoon-specific row split */
.prow.plat{flex-direction:row;align-items:center;justify-content:space-between;gap:12px}
.prow.plat .pmeta{flex:1;flex-direction:column;align-items:flex-start;gap:3px}
.plnum{display:flex;align-items:center;gap:12px}
.prow.unrel{opacity:.62}
.prow.unrel .pname{color:var(--muted)}

/* flag chips */
.flag{font:600 9.5px/1 var(--mono);letter-spacing:.02em;color:var(--muted);
  background:var(--surface-2);border:1px solid var(--line);border-radius:5px;
  padding:2px 5px;margin-left:4px;white-space:nowrap}
.flag.warn{color:rgba(var(--warm),1);border-color:rgba(var(--warm),.35);background:rgba(var(--warm),.07)}
.flag.mute{color:var(--faint)}

@media (max-width:540px){
  .head{flex-direction:column;align-items:flex-start;gap:7px}
  .prow.plat{flex-direction:column;align-items:stretch}
  .plnum{justify-content:space-between}
  .chip.wide{flex:1}
  .offedge{justify-content:flex-start}
}
"""

CSS_COMBINED = r"""
/* two-lens rows */
.lensline{display:flex;align-items:center;gap:10px}
.lensline + .lensline{margin-top:8px}
.lensline > .strip{flex:1 1 auto}
.lensline.pl{padding-top:7px;border-top:1px dashed var(--line-2)}
.lensline.pl .chip.wide{flex:0 0 168px}
.chip.wide.muted{opacity:.55} .chip.wide.muted .val{color:var(--faint)}
.chip.wide.unrel{border-style:dashed}
.chip.wide .lab{display:flex;align-items:center;gap:5px;flex-wrap:wrap}

/* consensus strip under the meter */
.consensus{display:flex;align-items:center;flex-wrap:wrap;gap:7px;
  padding:0 16px 12px;font:600 12px/1.3 var(--mono);color:var(--ink);
  font-variant-numeric:tabular-nums}
.consensus .muted{color:var(--muted);font-weight:500}
.consensus .dot{color:var(--faint)}
.consensus .ctag{font:700 9.5px/1 var(--sans);letter-spacing:.08em;text-transform:uppercase;
  padding:3px 7px;border-radius:6px;margin-left:auto}
.consensus .ctag.agree{color:rgba(var(--warm),1);background:rgba(var(--warm),.12);
  border:1px solid rgba(var(--warm),.3)}
.consensus .ctag.diverge{color:rgba(var(--cool),1);background:rgba(var(--cool),.12);
  border:1px solid rgba(var(--cool),.3)}
.consensus .ctag.na{color:var(--faint);background:var(--surface-2);border:1px solid var(--line)}

@media (max-width:540px){
  .lensline{flex-wrap:wrap}
  .lensline.pl .chip.wide{flex:1 1 auto}
  .consensus .ctag{margin-left:0}
  .compline{flex-wrap:wrap}
}

/* composite matchup score line */
.compline{display:flex;align-items:center;gap:9px;margin-top:8px;padding:5px 10px;
  border:1px solid var(--line-2);border-radius:9px}
.compline .clab{font:600 9px/1 var(--sans);text-transform:uppercase;letter-spacing:.09em;color:var(--muted)}
.compline .cval{font:700 16px/1 var(--mono);font-variant-numeric:tabular-nums}
.compline .cval.muted{color:var(--faint)}
.compline .ctier{font:600 10.5px/1 var(--sans);color:var(--muted);letter-spacing:.02em;text-transform:capitalize}

/* page footer */
.foot{margin-top:22px;text-align:center;font:500 11px/1.4 var(--mono);color:var(--faint)}
"""

def page_html(body):
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>MLB matchup leans — {SLATE_DATE}</title>"
            f"<style>{CSS}{CSS_COMBINED}</style></head><body>{body}</body></html>")

def render_site_html(xw_df, pl_df, throws):
    stamp = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    foot = f"<div class='foot'>built {stamp} · MLB StatsAPI + Baseball Savant</div>"
    games = _df_to_combined_games(xw_df, pl_df, throws) if xw_df is not None and not xw_df.empty else []
    if not games:
        body = (f"<div class='mx-wrap'>{_legend('MLB matchup leans — xwOBA + platoon OPS')}"
                f"<div class='legend'><div class='lg-title'>No paired probables to display for "
                f"{SLATE_DATE}.</div></div>{foot}</div>")
        return page_html(body)
    body = (f"<div class='mx-wrap'>{_legend('MLB matchup leans — xwOBA + platoon OPS')}"
            f"{build_combined(games)}{foot}</div>")
    return page_html(body)

# ============================================================
# MAIN
# ============================================================
def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SITE_DIR, exist_ok=True)

    print(f"Pulling slate for {SLATE_DATE} ...")
    slate_df = get_slate(SLATE_DATE, SPORT_ID)
    print(f"Games: {len(slate_df)}")
    if slate_df.empty:
        with open(SITE_PATH, "w") as f:
            f.write(render_site_html(pd.DataFrame(), None, {}))
        print(f"No MLB games for {SLATE_DATE} — wrote empty page to {SITE_PATH}.")
        return

    print("Loading Savant leaderboards (cached once/day) ...")
    batter_stat, _batter_bb, batter_cust = load_stat_lookups("batter")
    pitcher_stat, _pitcher_bb, _ = load_stat_lookups("pitcher")
    print(f"  batters: {len(batter_stat)} | pitchers: {len(pitcher_stat)}")

    # League baselines (PA-weighted, full batter population) for the odds-ratio matchup.
    _LB_MAP = {"xwoba": "xwOBA", "xba": "xBA", "xslg": "xSLG", "k_percent": "K%", "bb_percent": "BB%",
               "exit_velocity_avg": "EV", "launch_angle_avg": "LA°", "hard_hit_percent": "Hard Hit%"}
    league_baseline = {}
    _w = pd.to_numeric(batter_cust.get("pa"), errors="coerce")
    for raw, disp in _LB_MAP.items():
        if raw in batter_cust.columns:
            v = pd.to_numeric(batter_cust[raw], errors="coerce")
            m = v.notna() & _w.notna() & (_w > 0)
            league_baseline[disp] = round(float(np.average(v[m], weights=_w[m])), 3) if m.any() else np.nan
    print("  league baselines:", {k: league_baseline[k] for k in ["xwOBA", "Hard Hit%", "K%", "EV"] if k in league_baseline})

    print("Resolving lineups (gf -> roster fallback) ...")
    lineups, proj_flags, lineup_ids, prob_ids = {}, [], set(), set()
    for _, g in slate_df.iterrows():
        al, ap = resolve_lineup(g["game_pk"], "away", g["away_team_id"], batter_stat)
        hl, hp = resolve_lineup(g["game_pk"], "home", g["home_team_id"], batter_stat)
        lineups[g["game_pk"]] = (al, hl)
        proj_flags.append({"game_pk": g["game_pk"], "away_lineup_projected": ap,
                           "home_lineup_projected": hp})
        lineup_ids.update(al); lineup_ids.update(hl)
        for c in ("away_probable_pitcher_id", "home_probable_pitcher_id"):
            if pd.notna(g[c]): prob_ids.add(int(g[c]))
        time.sleep(REQUEST_DELAY)
    n_proj = sum(p["away_lineup_projected"] + p["home_lineup_projected"] for p in proj_flags)
    print(f"  projected (un-posted) lineups: {int(n_proj)} of {2 * len(slate_df)} sides")

    print("Loading player bio + vL/vR platoon splits ...")
    people = load_people(lineup_ids | prob_ids)
    player_splits_hit = load_splits(lineup_ids, "hitting")   # batters' OPS vs LHP / vs RHP
    player_splits_pit = load_splits(prob_ids, "pitching")    # pitchers' OPS-allowed vs LHB / vs RHB

    print("Assembling tables ...")
    pitchers_df = build_tables(slate_df, lineups, batter_stat, pitcher_stat, people)

    print("Building Statcast matchup ...")
    pitcher_rows_df, opp_hitters_df = segment_pitcher_blocks(pitchers_df, STATCAST_RATE_COLS)
    opp_lineup_agg_df = aggregate_lineup(opp_hitters_df, STATCAST_RATE_COLS, weighted=USE_WEIGHTED)
    matchup_df = build_matchup(pitcher_rows_df, opp_lineup_agg_df, STATCAST_RATE_COLS, league_baseline)
    matchup_df = add_composite_score(matchup_df, pitcher_rows_df, batter_cust, pitcher_stat)
    print(f"  matchup rows: {len(matchup_df)} (expected ~2 per game)")

    print("Building platoon OPS matchup ...")
    matchup_platoon_df, _detail = build_platoon(
        pitcher_rows_df, opp_hitters_df, player_splits_hit, player_splits_pit, people)
    print(f"  platoon rows: {len(matchup_platoon_df)}")

    # dump the day's leans for grade_leans.py (the ledger companion)
    if not matchup_df.empty:
        xw_path = os.path.join(DATA_DIR, f"leans_{SLATE_DATE}_xw.csv")
        matchup_df.to_csv(xw_path, index=False)
        print(f"Dumped {xw_path}")
        if matchup_platoon_df is not None and not matchup_platoon_df.empty:
            pl_path = os.path.join(DATA_DIR, f"leans_{SLATE_DATE}_pl.csv")
            matchup_platoon_df.to_csv(pl_path, index=False)
            print(f"Dumped {pl_path}")

    # render the consolidated card deck
    throws = {}
    if not pitcher_rows_df.empty:
        for _, pr in pitcher_rows_df.iterrows():
            throws[(pr["game_pk"], pr["Name"])] = pr.get("throws")
    with open(SITE_PATH, "w") as f:
        f.write(render_site_html(matchup_df, matchup_platoon_df, throws))
    print(f"Wrote {SITE_PATH}")

if __name__ == "__main__":
    main()
