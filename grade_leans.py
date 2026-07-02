#!/usr/bin/env python3
# ============================================================
# grade_leans.py — CI grading ledger for the matchup site
#
# Companion to build_site.py. Requires build_site.py to dump the day's
# model outputs (3-line patch, see MATCHUP_SITE.md / below):
#
#     os.makedirs("data", exist_ok=True)
#     matchup_df.to_csv(f"data/leans_{SLATE_DATE}_xw.csv", index=False)
#     if matchup_platoon_df is not None and not matchup_platoon_df.empty:
#         matchup_platoon_df.to_csv(f"data/leans_{SLATE_DATE}_pl.csv", index=False)
#
# This script then, on every CI run:
#   INGEST : any data/leans_*_xw.csv not yet ledgered -> pending rows.
#            Re-runs on the same date REFRESH still-pending rows (handles
#            SP scratches / lineup swaps up to first pitch). Graded rows
#            are never touched.
#   GRADE  : all pending rows via schedule?hydrate=linescore, one call per
#            date. Full-game + F5 (innings 1-5). Live games stay pending;
#            postponed/cancelled -> void.
#   REPORT : stdout (Actions log) + data/ledger_report.txt.
#
# Ledger persists at data/mlb_lean_ledger.csv — commit data/ back to the
# repo in the workflow (contents: write) so state survives between runs:
#
#     - name: Grade leans
#       run: python grade_leans.py
#     - name: Commit ledger
#       run: |
#         git config user.name  "github-actions[bot]"
#         git config user.email "github-actions[bot]@users.noreply.github.com"
#         git add data/
#         git diff --cached --quiet || git commit -m "ledger $(date -u +%F)"
#         git push
#
# SP-vs-lineup weight fit: logs d_lineup / d_sp per game; once >= N_FIT_MIN
# graded F5 decisions accumulate, fits logit(home F5 win) ~ d_lineup + d_sp.
# Symmetric log5 implies b_sp/b_lineup ≈ 1; a stable departure is the
# reweight: net_w = d_lineup + w·d_sp.
# ============================================================
import glob
import math
import os
import re
import time

import numpy as np
import pandas as pd
import requests

DATA_DIR    = os.environ.get("DATA_DIR", "data")
LEDGER_PATH = os.path.join(DATA_DIR, "mlb_lean_ledger.csv")
REPORT_PATH = os.path.join(DATA_DIR, "ledger_report.txt")
MODEL_TAG   = os.environ.get("MODEL_TAG", "xw+plat_consol_v1")
N_FIT_MIN   = 120
_FINAL  = {"Final", "Game Over", "Completed Early"}
_VOID   = {"Postponed", "Cancelled"}

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

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})

def _hj(url, params=None, tries=4):
    for k in range(tries):
        try:
            r = session.get(url, params=params, timeout=30); r.raise_for_status()
            return r.json()
        except Exception:
            if k == tries - 1: raise
            time.sleep(0.6 * (2 ** k))

def _ab(name): return ABBR.get(name, str(name or "")[:3].upper())
def _fx(v):
    try:
        v = float(v); return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None

LEDGER_COLS = [
    "game_pk","game_date","away","home","away_sp","home_sp","model_tag",
    "B_home","B_away","P_awaySP","P_homeSP","d_lineup","d_sp",
    "home_off_edge","away_off_edge","xw_net","xw_lean","xw_delta",
    "ops_net","ops_lean","ops_delta","ops_valid","consensus",
    "status","full_away","full_home","f5_away","f5_home",
    "xw_full","xw_f5","ops_full","ops_f5",
]
MODEL_FIELDS = LEDGER_COLS[1:23]   # everything refreshable while pending

def load_ledger():
    if os.path.exists(LEDGER_PATH):
        led = pd.read_csv(LEDGER_PATH)
        for c in LEDGER_COLS:
            if c not in led.columns: led[c] = np.nan
        return led[LEDGER_COLS]
    return pd.DataFrame(columns=LEDGER_COLS)

# ---- INGEST ------------------------------------------------------------
def rows_from_dump(xw_df, pl_df):
    pl_map = {}
    if pl_df is not None and not pl_df.empty:
        for _, r in pl_df.iterrows():
            pl_map[(int(r["game_pk"]), r["side"])] = r
    out = []
    for gpk, gg in xw_df.groupby("game_pk", sort=False):
        gpk = int(gpk)
        a = gg[gg["side"] == "away"]; h = gg[gg["side"] == "home"]
        if not len(a) or not len(h): continue
        a, h = a.iloc[0], h.iloc[0]
        home_team, away_team = _ab(a["opp_team"]), _ab(h["opp_team"])
        B_home, P_aSP = _fx(a.get("opp_xwOBA")), _fx(a.get("pit_xwOBA"))
        B_away, P_hSP = _fx(h.get("opp_xwOBA")), _fx(h.get("pit_xwOBA"))
        home_off, away_off = _fx(a.get("edge_xwOBA")), _fx(h.get("edge_xwOBA"))
        if home_off is None or away_off is None: continue
        xw_net = home_off - away_off
        d_lu = (B_home - B_away) if None not in (B_home, B_away) else np.nan
        d_sp = (P_aSP - P_hSP)   if None not in (P_aSP, P_hSP)   else np.nan
        pa, ph = pl_map.get((gpk, "away")), pl_map.get((gpk, "home"))
        ops_net = ops_lean = ops_delta = None; ops_valid = False
        if pa is not None and ph is not None:
            eh, ea = _fx(pa.get("edge_OPS")), _fx(ph.get("edge_OPS"))
            if eh is not None and ea is not None:
                ops_net, ops_delta = eh - ea, abs(eh - ea)
                ops_lean  = home_team if eh >= ea else away_team
                ops_valid = bool(pa.get("reliable")) and bool(ph.get("reliable"))
        xw_lean = home_team if xw_net >= 0 else away_team
        consensus = "NA" if not ops_valid else ("AGREE" if ops_lean == xw_lean else "DIVERGE")
        out.append(dict(
            game_pk=gpk, game_date=str(a.get("game_date")), away=away_team, home=home_team,
            away_sp=a.get("pitcher"), home_sp=h.get("pitcher"), model_tag=MODEL_TAG,
            B_home=B_home, B_away=B_away, P_awaySP=P_aSP, P_homeSP=P_hSP,
            d_lineup=d_lu, d_sp=d_sp,
            home_off_edge=home_off, away_off_edge=away_off,
            xw_net=round(xw_net, 4), xw_lean=xw_lean, xw_delta=round(abs(xw_net), 4),
            ops_net=(round(ops_net, 4) if ops_net is not None else np.nan),
            ops_lean=ops_lean,
            ops_delta=(round(ops_delta, 4) if ops_delta is not None else np.nan),
            ops_valid=ops_valid, consensus=consensus,
            status="pending", full_away=np.nan, full_home=np.nan,
            f5_away=np.nan, f5_home=np.nan,
            xw_full=None, xw_f5=None, ops_full=None, ops_f5=None,
        ))
    return out

def ingest(led):
    n_new = n_ref = 0
    for xw_path in sorted(glob.glob(os.path.join(DATA_DIR, "leans_*_xw.csv"))):
        pl_path = xw_path.replace("_xw.csv", "_pl.csv")
        xw = pd.read_csv(xw_path)
        pl = pd.read_csv(pl_path) if os.path.exists(pl_path) else None
        for row in rows_from_dump(xw, pl):
            hit = led.index[pd.to_numeric(led["game_pk"], errors="coerce") == row["game_pk"]]
            if len(hit) == 0:
                add = pd.DataFrame([row])[LEDGER_COLS]
                led = add if led.empty else pd.concat([led, add], ignore_index=True)
                n_new += 1
            elif led.at[hit[0], "status"] == "pending":
                for k in MODEL_FIELDS:                    # refresh scratches pre-lock
                    led.at[hit[0], k] = row[k]
                n_ref += 1
    print(f"ingest: +{n_new} new, {n_ref} pending refreshed ({len(led)} total)")
    return led

# ---- GRADE -------------------------------------------------------------
def _linescores_for(day):
    data = _hj("https://statsapi.mlb.com/api/v1/schedule",
               {"sportId": 1, "date": day, "hydrate": "linescore"})
    out = {}
    for db in data.get("dates", []):
        for g in db.get("games", []):
            out[int(g["gamePk"])] = g
    return out

def _f5(innings, side):
    if innings is None or len(innings) < 5: return None
    tot = 0
    for inn in innings[:5]:
        r = (inn.get(side) or {}).get("runs")
        if r is None: return None
        tot += int(r)
    return tot

def _wlt(lean, away, home, ra, rh, allow_tie):
    if lean is None or (isinstance(lean, float) and math.isnan(lean)): return None
    if ra == rh: return "T" if allow_tie else None
    return "W" if lean == (home if rh > ra else away) else "L"

def grade(led):
    pend = led[led["status"] == "pending"]
    if pend.empty:
        print("grade: nothing pending."); return led
    n_g = n_v = 0
    for day in sorted(pend["game_date"].dropna().unique()):
        games = _linescores_for(day)
        for idx in pend[pend["game_date"] == day].index:
            g = games.get(int(led.at[idx, "game_pk"]))
            if g is None: continue
            state = (g.get("status") or {}).get("detailedState", "")
            if state in _VOID:
                led.at[idx, "status"] = "void"; n_v += 1; continue
            if state not in _FINAL:
                continue
            ls = g.get("linescore") or {}
            fa = (ls.get("teams", {}).get("away", {}) or {}).get("runs")
            fh = (ls.get("teams", {}).get("home", {}) or {}).get("runs")
            if fa is None or fh is None: continue
            f5a, f5h = _f5(ls.get("innings"), "away"), _f5(ls.get("innings"), "home")
            aw, hm = led.at[idx, "away"], led.at[idx, "home"]
            led.at[idx, "full_away"], led.at[idx, "full_home"] = fa, fh
            led.at[idx, "f5_away"],   led.at[idx, "f5_home"]   = f5a, f5h
            led.at[idx, "xw_full"] = _wlt(led.at[idx, "xw_lean"], aw, hm, fa, fh, False)
            if f5a is not None:
                led.at[idx, "xw_f5"] = _wlt(led.at[idx, "xw_lean"], aw, hm, f5a, f5h, True)
            if bool(led.at[idx, "ops_valid"]):
                led.at[idx, "ops_full"] = _wlt(led.at[idx, "ops_lean"], aw, hm, fa, fh, False)
                if f5a is not None:
                    led.at[idx, "ops_f5"] = _wlt(led.at[idx, "ops_lean"], aw, hm, f5a, f5h, True)
            led.at[idx, "status"] = "graded"; n_g += 1
    print(f"grade: {n_g} graded, {n_v} void, "
          f"{int((led['status'] == 'pending').sum())} still pending")
    return led

# ---- REPORT ------------------------------------------------------------
def _rec(s):
    s = s.dropna()
    w, l, t = int((s == "W").sum()), int((s == "L").sum()), int((s == "T").sum())
    base = f"{w}-{l}" + (f"-{t}" if t else "")
    return f"{base}  ({w/(w+l):.3f})" if (w + l) else base

def _logit_fit(X, y, iters=60):
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-X @ b)); Wd = p * (1 - p)
        H = X.T @ (X * Wd[:, None]) + np.eye(X.shape[1]) * 1e-9
        step = np.linalg.solve(H, X.T @ (y - p))
        b += step
        if np.max(np.abs(step)) < 1e-10: break
    p = 1.0 / (1.0 + np.exp(-X @ b)); Wd = p * (1 - p)
    cov = np.linalg.inv(X.T @ (X * Wd[:, None]) + np.eye(X.shape[1]) * 1e-9)
    return b, np.sqrt(np.diag(cov))

def report(led):
    lines = []
    say = lines.append
    g = led[(led["status"] == "graded") & (led["model_tag"] == MODEL_TAG)].copy()
    if g.empty:
        say("no graded games yet.")
    else:
        say(f"LEAN LEDGER — {len(g)} graded games  [{MODEL_TAG}]")
        say(f"xwOBA lean   full: {_rec(g['xw_full'])}   F5: {_rec(g['xw_f5'])}")
        ov = g[g["ops_valid"] == True]                                # noqa: E712
        if len(ov):
            say(f"platoon lean full: {_rec(ov['ops_full'])}   F5: {_rec(ov['ops_f5'])}   (reliable-only, n={len(ov)})")
            say(f"xwOBA on same subset  full: {_rec(ov['xw_full'])}   F5: {_rec(ov['xw_f5'])}")
        if len(g) >= 9:
            g["_terc"] = pd.qcut(g["xw_delta"], 3, labels=["low", "mid", "hi"], duplicates="drop")
            say("xwOBA F5 by |Δ| tercile:")
            for lab, gg in g.groupby("_terc", observed=True):
                say(f"  {lab:3}  {_rec(gg['xw_f5'])}   (Δ {gg['xw_delta'].min():.3f}–{gg['xw_delta'].max():.3f}, n={len(gg)})")
        dv = g[g["consensus"] == "DIVERGE"]
        if len(dv):
            say(f"DIVERGE h2h (F5): xwOBA {int((dv['xw_f5']=='W').sum())} — "
                f"platoon {int((dv['ops_f5']=='W').sum())}  (n={len(dv)})")
        f5d = g.dropna(subset=["f5_away", "f5_home"])
        dec = f5d[f5d["f5_home"] != f5d["f5_away"]]
        if len(dec):
            say(f"home F5 baseline: {(dec['f5_home'] > dec['f5_away']).mean():.3f}  (n={len(dec)})")
        fit = g.dropna(subset=["d_lineup", "d_sp", "f5_away", "f5_home"])
        fit = fit[fit["f5_home"] != fit["f5_away"]]
        say(f"weight fit: {len(fit)} usable F5 decisions (gate {N_FIT_MIN})")
        if len(fit) >= N_FIT_MIN:
            dlu = (fit["d_lineup"] - fit["d_lineup"].mean()) / fit["d_lineup"].std()
            dsp = (fit["d_sp"]     - fit["d_sp"].mean())     / fit["d_sp"].std()
            X = np.column_stack([np.ones(len(fit)), dlu.values, dsp.values])
            y = (fit["f5_home"] > fit["f5_away"]).astype(float).values
            b, se = _logit_fit(X, y)
            raw_lu = b[1] / fit["d_lineup"].std(); raw_sp = b[2] / fit["d_sp"].std()
            ratio = raw_sp / raw_lu if raw_lu else np.nan
            say(f"  b_lineup={b[1]:+.3f}±{se[1]:.3f}  b_sp={b[2]:+.3f}±{se[2]:.3f}  HFA={b[0]:+.3f}")
            say(f"  implied w = b_sp/b_lineup = {ratio:+.2f}  (symmetric log5 ⇒ ≈ +1.00)")
    txt = "\n".join(lines)
    print("=" * 60); print(txt); print("=" * 60)
    with open(REPORT_PATH, "w") as f:
        f.write(txt + "\n")

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    led = load_ledger()
    led = ingest(led)
    led = grade(led)
    led.to_csv(LEDGER_PATH, index=False)
    report(led)

if __name__ == "__main__":
    main()
