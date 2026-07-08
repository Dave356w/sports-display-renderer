# =============================================================================
# MARKET BACKFILL — attach ESPN/DK closing moneylines to the grading ledger
# =============================================================================
# Drop-in for the grading harness (notebook cell or imported module).
#
# WORKFLOW (daily, after grading):
#   df = pd.read_csv(LEDGER_CSV)
#   df = attach_market(df)          # idempotent; only touches settled rows
#   df.to_csv(LEDGER_CSV, index=False)
#   vs_market_summary(df)           # prints the scoreboard; returns dict for grades.html
#
# COLUMNS ADDED:
#   gamePk, espn_id, open_away_ml, open_home_ml, close_away_ml, close_home_ml,
#   close_p_home  (devigged two-way home win prob at close)
#
# JOIN LOGIC (validated 59/59 on 07-02..07-07 slate):
#   ledger row -> MLB StatsAPI schedule (date + away + home), doubleheaders
#   disambiguated by probable-pitcher surname; join VERIFIED by final score
#   (mismatch = hard skip + log, never a guess). gamePk -> ESPN event by
#   date + teams, DH disambiguated by final score then start-time proximity.
#   ESPN core API /odds list filtered to provider id 100 (DraftKings).
#   NOTE: direct /odds/100 path 404s as of 2026-07; moneyLine is a dict —
#   read .american. 'close' is only trustworthy on settled (state=post) games.
#
# FAILURE MODE: any row that can't be joined or verified keeps NaN market
# columns and is reported in the returned skip log. No silent defaults.
# =============================================================================

import json
import time
import unicodedata
import datetime as dt
import urllib.request

import numpy as np
import pandas as pd

# ---- COLMAP: adjust to the ledger CSV's actual column names ----------------
COL = dict(
    date="game_date",       # 'YYYY-MM-DD'
    away="away",            # ledger team abbr (ARI-style)
    home="home",
    p_away="away_sp",       # away starter full name (for DH disambiguation)
    p_home="home_sp",
    away_runs="full_away",  # final score; NaN/None = pending
    home_runs="full_home",
    xw_team="xw_lean",      # lean side abbrs
    pl_team="ops_lean",
    pl_reliable="ops_valid",
)

THROTTLE_S = 0.15
LEDGER2SA = {"ARI": "AZ"}                       # ledger -> StatsAPI abbr
ESPN2SA = {"CHW": "CWS", "ARI": "AZ", "OAK": "ATH"}  # ESPN -> StatsAPI abbr

MARKET_COLS = ["gamePk", "espn_id", "open_away_ml", "open_home_ml",
               "close_away_ml", "close_home_ml", "close_p_home"]


# ---------------------------------------------------------------- helpers ---
def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def _dig(d, *ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _norm(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _amer(x):
    if x is None:
        return None
    try:
        return int(str(x).replace("+", ""))
    except ValueError:
        return None


def _imp(ml):
    return 100.0 / (ml + 100.0) if ml > 0 else -ml / (-ml + 100.0)


def _dec(ml):
    return 1.0 + (ml / 100.0 if ml > 0 else 100.0 / (-ml))


# ------------------------------------------------------------ data pulls ----
def _statsapi_day(date):
    js = _get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
              f"&hydrate=probablePitcher,team")
    out = []
    for dd in js.get("dates", []):
        for gm in dd.get("games", []):
            out.append(dict(
                gamePk=gm["gamePk"], gameDate=gm["gameDate"],
                away=_dig(gm, "teams", "away", "team", "abbreviation"),
                home=_dig(gm, "teams", "home", "team", "abbreviation"),
                p_away=_dig(gm, "teams", "away", "probablePitcher", "fullName"),
                p_home=_dig(gm, "teams", "home", "probablePitcher", "fullName"),
                away_score=_dig(gm, "teams", "away", "score"),
                home_score=_dig(gm, "teams", "home", "score"),
            ))
    return out


def _espn_day(date):
    ds = date.replace("-", "")
    sb = _get(f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={ds}")
    out = {}
    for ev in sb.get("events", []):
        comp = ev["competitions"][0]
        t = {c["homeAway"]: ESPN2SA.get(c["team"]["abbreviation"], c["team"]["abbreviation"])
             for c in comp["competitors"]}
        sc = {c["homeAway"]: c.get("score") for c in comp["competitors"]}
        out.setdefault((t["away"], t["home"]), []).append(dict(
            eid=ev["id"], start=ev["date"],
            away_sc=sc["away"], home_sc=sc["home"],
            state=_dig(comp, "status", "type", "state"),
        ))
    return out


def _espn_close(eid):
    odds = _get(f"https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb"
                f"/events/{eid}/competitions/{eid}/odds")
    dk = next((i for i in odds.get("items", [])
               if str(_dig(i, "provider", "id")) == "100"), None)
    if dk is None:
        return None
    return dict(
        open_home_ml=_amer(_dig(dk, "homeTeamOdds", "open", "moneyLine", "american")),
        open_away_ml=_amer(_dig(dk, "awayTeamOdds", "open", "moneyLine", "american")),
        close_home_ml=_amer(_dig(dk, "homeTeamOdds", "close", "moneyLine", "american")),
        close_away_ml=_amer(_dig(dk, "awayTeamOdds", "close", "moneyLine", "american")),
    )


# ------------------------------------------------------------- main entry ---
def attach_market(df, col=COL, verbose=True):
    """Idempotently attach gamePk + DK closing MLs to settled ledger rows.

    Returns the modified DataFrame. Skipped rows keep NaN and are listed in
    df.attrs['market_skips'] as (index, reason) tuples.
    """
    for c in MARKET_COLS:
        if c not in df.columns:
            df[c] = np.nan
    # dtype coercion must be unconditional: CSV round-trips reload all-NaN
    # ID columns as float64, which rejects string/int assignment.
    df["espn_id"] = df["espn_id"].astype("string")
    df["gamePk"] = df["gamePk"].astype("Int64")
    settled = df[col["away_runs"]].notna() & df[col["home_runs"]].notna()
    todo = df.index[settled & df["close_home_ml"].isna()]
    if len(todo) == 0:
        if verbose:
            print("market backfill: nothing to do")
        df.attrs["market_skips"] = []
        return df

    dates = sorted(df.loc[todo, col["date"]].unique())
    sched = {}
    espn = {}
    for d in dates:
        sched[d] = _statsapi_day(d)
        time.sleep(THROTTLE_S)
        espn[d] = _espn_day(d)
        time.sleep(THROTTLE_S)

    skips = []
    for i in todo:
        r = df.loc[i]
        d = r[col["date"]]
        aw = LEDGER2SA.get(r[col["away"]], r[col["away"]])
        hm = LEDGER2SA.get(r[col["home"]], r[col["home"]])
        a_runs, h_runs = int(r[col["away_runs"]]), int(r[col["home_runs"]])

        # --- StatsAPI join (gamePk), DH disambiguation by pitcher surname
        cands = [g for g in sched[d] if g["away"] == aw and g["home"] == hm]
        if len(cands) > 1:
            sur = _norm(str(r[col["p_away"]])).split()[-1]
            narrowed = [g for g in cands if g["p_away"] and sur in _norm(g["p_away"])]
            if len(narrowed) != 1:
                sur = _norm(str(r[col["p_home"]])).split()[-1]
                narrowed = [g for g in cands if g["p_home"] and sur in _norm(g["p_home"])]
            cands = narrowed
        if len(cands) != 1:
            skips.append((i, f"statsapi join ambiguous ({len(cands)} cands)"))
            continue
        g = cands[0]
        if (g["away_score"], g["home_score"]) != (a_runs, h_runs):
            skips.append((i, f"score mismatch statsapi {g['away_score']}-{g['home_score']}"
                             f" vs ledger {a_runs}-{h_runs}"))
            continue

        # --- ESPN event join, DH disambiguation by score then start time
        evs = espn[d].get((aw, hm), [])
        if len(evs) > 1:
            byscore = [e for e in evs
                       if (str(e["away_sc"]), str(e["home_sc"])) == (str(a_runs), str(h_runs))]
            if len(byscore) == 1:
                evs = byscore
            else:
                gd = dt.datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00"))
                evs = sorted(evs, key=lambda e: abs(
                    (dt.datetime.fromisoformat(e["start"].replace("Z", "+00:00")) - gd)
                    .total_seconds()))[:1]
        if len(evs) != 1:
            skips.append((i, "espn event join failed"))
            continue
        if evs[0]["state"] != "post":
            skips.append((i, "espn event not settled; close unreliable"))
            continue

        ml = _espn_close(evs[0]["eid"])
        time.sleep(THROTTLE_S)
        if ml is None or ml["close_home_ml"] is None or ml["close_away_ml"] is None:
            skips.append((i, "no DK close on event"))
            continue

        ph, pa = _imp(ml["close_home_ml"]), _imp(ml["close_away_ml"])
        df.loc[i, "gamePk"] = g["gamePk"]
        df.loc[i, "espn_id"] = evs[0]["eid"]
        for k, v in ml.items():
            df.loc[i, k] = v
        df.loc[i, "close_p_home"] = ph / (ph + pa)

    df.attrs["market_skips"] = skips
    if verbose:
        done = settled.sum() - len(skips)
        print(f"market backfill: {len(todo) - len(skips)} attached, {len(skips)} skipped")
        for i, why in skips:
            print(f"  SKIP row {i}: {why}")
    return df


# --------------------------------------------------------------- analysis ---
def vs_market_summary(df, col=COL):
    """Vs-market scoreboard for both models. Returns dict for grades.html chips."""
    d = df[df["close_p_home"].notna()].copy()
    d["winner"] = np.where(d[col["home_runs"]] > d[col["away_runs"]],
                           d[col["home"]], d[col["away"]])
    d["fav"] = np.where(d["close_p_home"] >= 0.5, d[col["home"]], d[col["away"]])
    out = {}
    specs = [("xwOBA", d, col["xw_team"]),
             ("platoon", d[d[col["pl_reliable"]] == True], col["pl_team"])]  # noqa: E712
    for label, rows, key in specs:
        rows = rows[rows[key].notna()]
        n = len(rows)
        if n == 0:
            continue
        p_side = np.where(rows[key] == rows[col["home"]],
                          rows["close_p_home"], 1 - rows["close_p_home"])
        w = (rows[key] == rows["winner"]).sum()
        exp, var = p_side.sum(), (p_side * (1 - p_side)).sum()
        z = (w - exp) / np.sqrt(var)
        ml = np.where(rows[key] == rows[col["home"]],
                      rows["close_home_ml"], rows["close_away_ml"])
        pnl = np.where(rows[key] == rows["winner"],
                       [_dec(m) - 1 for m in ml], -1.0).sum()
        fav_agree = (rows[key] == rows["fav"]).mean()
        fav_w = (rows["fav"] == rows["winner"]).sum()
        out[label] = dict(n=int(n), w=int(w), exp=round(float(exp), 1),
                          z=round(float(z), 2), roi_units=round(float(pnl), 2),
                          fav_agree=round(float(fav_agree), 3),
                          fav_baseline=f"{fav_w}-{n - fav_w}")
        print(f"{label}: {w}-{n - w} | market-expected {exp:.1f}W -> z {z:+.2f} | "
              f"ROI {pnl:+.2f}u | agrees w/ fav {fav_agree:.0%} | fav baseline {fav_w}-{n - fav_w}")
    return out
