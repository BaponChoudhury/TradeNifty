"""Forward-testing signal logger. Run once daily after NSE close (~18:30 IST).

Records signals BEFORE outcomes exist, so accumulated results cannot be
overfit. Appends one row per run to forward_log.csv. Idempotent per date.

Logged signals:
  regime   — NIFTY close, 20d realized vol, 15%-vol-target weight, 200d-MA
             state, universe breadth (37 large caps above 100d MA)
  quiet7   — top-7 large-caps by 10d/60d range-compression rank
  coilspring — BAJFINANCE Coiled Spring v2 state (setup/order/position)

Usage:  uv run --python 3.13 --with "git+https://github.com/rongardF/tvdatafeed" --with pandas,numpy python forward_signals.py
Report: add  --report  to print paper-tracking stats from the log so far.
"""
import os
import sys
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "forward_log.csv")

LARGECAPS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK",
             "KOTAKBANK", "LT", "ITC", "HINDUNILVR", "BHARTIARTL", "MARUTI",
             "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "SUNPHARMA", "DRREDDY", "CIPLA",
             "ASIANPAINT", "TITAN", "BAJFINANCE", "BAJAJFINSV", "HCLTECH", "WIPRO",
             "TECHM", "NTPC", "POWERGRID", "ONGC", "ULTRACEMCO", "GRASIM",
             "HINDALCO", "BPCL", "EICHERMOT", "HEROMOTOCO", "NESTLEIND", "BRITANNIA"]


def fetch_all():
    from tvDatafeed import TvDatafeed, Interval
    tv = TvDatafeed()
    out = {}
    for sym in LARGECAPS + ["NIFTY"]:
        for attempt in range(3):
            try:
                df = tv.get_hist(sym, "NSE", Interval.in_daily, n_bars=300)
                if df is not None and len(df) > 250:
                    df.index = pd.to_datetime(df.index).normalize()
                    out[sym] = df
                    break
            except Exception as e:
                print(f"warn: {sym} attempt {attempt+1} failed ({e})", file=sys.stderr)
            import time
            time.sleep(2)
    return out


def compute_row(data):
    nifty = data["NIFTY"]["close"]
    nopen = data["NIFTY"]["open"]
    ret = nifty.pct_change()
    rvol = float(ret.rolling(20).std().iloc[-1] * np.sqrt(252))
    row = {
        "run_ts": pd.Timestamp.now().isoformat(timespec="seconds"),
        "bar_date": str(nifty.index[-1].date()),
        "nifty_open": float(nopen.iloc[-1]),
        # realized overnight return: yesterday's close -> today's open
        "overnight_ret_pct": round(float(nopen.iloc[-1] / nifty.iloc[-2] - 1) * 100, 4),
        "day_ret_pct": round(float(nifty.iloc[-1] / nopen.iloc[-1] - 1) * 100, 4),
        "nifty_close": float(nifty.iloc[-1]),
        "rvol20": round(rvol, 4),
        "voltgt15_w": round(min(1.0, 0.15 / rvol), 3),
        "above_ma200": bool(nifty.iloc[-1] > nifty.rolling(200).mean().iloc[-1]),
    }
    above = {s: float(d["close"].iloc[-1] > d["close"].rolling(100).mean().iloc[-1])
             for s, d in data.items() if s != "NIFTY"}
    row["breadth"] = round(float(np.mean(list(above.values()))), 3)

    # market volume z: mean of per-stock log-volume z-scores (60d) —
    # tracks the unvalidated "green + high market volume" overnight variant
    mvz = []
    for s, d in data.items():
        if s == "NIFTY":
            continue
        logv = np.log(d["volume"].replace(0, np.nan))
        z = (logv - logv.rolling(60).mean()) / logv.rolling(60).std()
        if np.isfinite(z.iloc[-1]):
            mvz.append(float(z.iloc[-1]))
    row["mktvol_z"] = round(float(np.mean(mvz)), 3) if mvz else np.nan

    comp = {}
    for s, d in data.items():
        if s == "NIFTY":
            continue
        rp = (d["high"] - d["low"]) / d["close"]
        comp[s] = float(rp.rolling(10).mean().iloc[-1] / rp.rolling(60).mean().iloc[-1])
    row["quiet7"] = "|".join(sorted(comp, key=comp.get)[:7])

    # institutional/retail F&O positioning (NSE participant-wise OI)
    try:
        import io
        import urllib.request
        raw = None
        probe = pd.Timestamp(row["bar_date"])
        for _ in range(5):  # latest published file (posted evenings; skip holidays)
            tag = probe.strftime("%d%m%Y")
            url = f"https://archives.nseindia.com/content/nsccl/fao_participant_oi_{tag}.csv"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
                row["positioning_date"] = str(probe.date())
                break
            except Exception:
                probe -= pd.tseries.offsets.BDay(1)
        if raw is None:
            raise RuntimeError("no participant file found in last 5 business days")
        lines = raw.splitlines(keepends=True)
        hdr = next(i for i, ln in enumerate(lines) if "Client Type" in ln)
        pdf = pd.read_csv(io.StringIO("".join(lines[hdr:])))
        pdf.columns = [c.strip() for c in pdf.columns]
        pdf["Client Type"] = pdf["Client Type"].astype(str).str.strip()
        for who, key in [("Client", "retail_ratio"), ("FII", "fii_ratio")]:
            r_ = pdf[pdf["Client Type"] == who].iloc[0]
            fl, fs = float(r_["Future Index Long"]), float(r_["Future Index Short"])
            row[key] = round(fl / (fl + fs), 4) if fl + fs > 0 else np.nan
        hist_path = os.path.join(BASE, "fii_positioning.csv")
        if os.path.exists(hist_path):
            hist = pd.read_csv(hist_path, parse_dates=["date"], index_col="date")
            cr = hist["Client_ratio"].dropna().tail(252)
            row["retail_pctile_1y"] = round(float((cr < row["retail_ratio"]).mean()), 3)
            if str(row["bar_date"]) not in hist.index.strftime("%Y-%m-%d").tolist():
                add = pd.DataFrame([{"Client_ratio": row["retail_ratio"],
                                     "FII_ratio": row["fii_ratio"]}],
                                   index=[pd.Timestamp(row["bar_date"])])
                add.index.name = "date"
                pd.concat([hist, add]).to_csv(hist_path)
    except Exception as e:
        print(f"warn: positioning fetch failed ({e})", file=sys.stderr)
        row["retail_ratio"] = row["fii_ratio"] = row["retail_pctile_1y"] = np.nan

    d = data["BAJFINANCE"]
    c, h, l = d["close"], d["high"], d["low"]
    rng = (h - l).replace(0, np.nan)
    rp = rng / c
    rangez = (rp - rp.rolling(60).mean()) / rp.rolling(60).std()
    logv = np.log(d["volume"].replace(0, np.nan))
    volz = (logv - logv.rolling(60).mean()) / logv.rolling(60).std()
    mom20 = c.pct_change(20)
    bd20 = c.iloc[-1] < c.shift(1).rolling(20).min().iloc[-1]
    setup = (mom20.iloc[-1] > 0 and not bd20 and rangez.iloc[-1] < 0
             and -0.9 < volz.iloc[-1] < 0.4 and c.pct_change(5).iloc[-1] < 0.016)
    row.update({
        "cs_setup": bool(setup),
        "cs_trigger": round(float(h.iloc[-1]), 2) if setup else "",
        "cs_stop": round(float(l.iloc[-1]), 2) if setup else "",
        "bajfin_close": float(c.iloc[-1]),
    })
    return row


def report():
    if not os.path.exists(LOG):
        print("no log yet")
        return
    log = pd.read_csv(LOG)
    print(f"log rows: {len(log)}  span: {log['bar_date'].iloc[0]} .. {log['bar_date'].iloc[-1]}")
    if len(log) < 6:
        print("need more history for paper stats")
        return
    log["nifty_ret"] = pd.to_numeric(log["nifty_close"]).pct_change()
    strat = (log["voltgt15_w"].shift(1) * log["nifty_ret"]).dropna()
    bench = log["nifty_ret"].dropna()
    for name, r in [("voltgt15", strat), ("buy&hold", bench)]:
        eq = (1 + r).cumprod()
        print(f"{name:9s} total {eq.iloc[-1]-1:+.2%}  vol {r.std()*np.sqrt(252):.1%}  "
              f"maxDD {(eq/eq.cummax()-1).min():+.2%}")
    setups = log[log["cs_setup"] == True]  # noqa: E712
    print(f"coilspring setups logged: {len(setups)}")


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
        sys.exit(0)
    data = fetch_all()
    if "NIFTY" not in data or len(data) < 25:
        print("fetch too incomplete; aborting without logging", file=sys.stderr)
        sys.exit(1)
    row = compute_row(data)
    # never log a partial session: if the latest bar is today's and NSE is
    # still open (before 15:35 IST), skip — tonight's scheduled run logs it
    now_ist = pd.Timestamp.now(tz="Asia/Kolkata")
    if str(row["bar_date"]) == str(now_ist.date()) and (now_ist.hour, now_ist.minute) < (15, 35):
        print(f"market still open ({now_ist:%H:%M} IST) — skipping partial-day log")
        sys.exit(0)
    log = pd.read_csv(LOG) if os.path.exists(LOG) else None
    if log is not None and str(row["bar_date"]) in set(log["bar_date"].astype(str)):
        print(f"already logged for {row['bar_date']}")
        sys.exit(0)

    # ---- verdict columns: did last night's decision work? ----
    COST_PCT = 0.035          # futures round trip, % of notional
    LOT = 75
    row["signal_tonight"] = "HOLD" if row["day_ret_pct"] > 0 else "SKIP"
    prev_cum = 0.0
    held = None
    if log is not None and len(log):
        prev = log.iloc[-1]
        if "cum_pnl_1lot_rs" in log.columns and pd.notna(prev.get("cum_pnl_1lot_rs")):
            prev_cum = float(prev["cum_pnl_1lot_rs"])
        try:
            held = float(prev["day_ret_pct"]) > 0    # last night's decision
        except (ValueError, TypeError):
            held = None
    if held is None:
        row["result"] = ""
        row["cum_pnl_1lot_rs"] = prev_cum
    elif held:
        gross = float(row["overnight_ret_pct"])       # last night's actual gap %
        net = gross - COST_PCT
        notional = LOT * float(prev["nifty_close"])   # entry ~ last close
        pnl = round(net / 100 * notional)
        row["held_last_night"] = True
        row["net_overnight_pct"] = round(net, 4)
        row["result"] = "WIN" if net > 0 else "LOSS"
        row["pnl_1lot_rs"] = pnl
        row["cum_pnl_1lot_rs"] = round(prev_cum + pnl)
    else:
        row["held_last_night"] = False
        row["result"] = f"SKIPPED (gap was {row['overnight_ret_pct']:+.2f}%)"
        row["pnl_1lot_rs"] = 0
        row["cum_pnl_1lot_rs"] = round(prev_cum)

    log = pd.concat([log, pd.DataFrame([row])], ignore_index=True) if log is not None else pd.DataFrame([row])
    log.to_csv(LOG, index=False)
    print(f"logged {row['bar_date']}: nifty={row['nifty_close']:.1f} w={row['voltgt15_w']} "
          f"breadth={row['breadth']} cs_setup={row['cs_setup']}")
