# Daily signal logger — free cloud scheduling via GitHub Actions

Runs `forward_signals.py` every trading day at 19:15 IST on GitHub's servers
(free tier) and commits the updated `forward_log.csv` back to the repo.
Your PC does not need to be on.

## One-time setup (~5 minutes)

1. Create a GitHub account if you don't have one (github.com — free).
2. Create a new **private** repository, e.g. `nse-signal-log`.
3. Upload the contents of this folder to the repo (drag-and-drop on
   github.com works: `forward_signals.py`, `fii_positioning.csv`,
   `forward_log.csv`, and the `.github/workflows/daily-log.yml` file —
   keep the folder structure for the workflow file).
4. In the repo: Settings → Actions → General → Workflow permissions →
   select **Read and write permissions** → Save.
5. Test it: Actions tab → "daily-signal-log" → **Run workflow**. A green
   check and a new commit updating `forward_log.csv` = working.

From then on it runs automatically Mon–Fri. View the growing log anytime at
`forward_log.csv` in the repo (or pull it locally).

## What gets logged daily
- `overnight_ret_pct` — last night's realized close→open gap (the outcome)
- `day_ret_pct` — today's session green/red (tonight's hold/skip signal)
- `voltgt15_w`, `breadth`, `retail_ratio`, `retail_pctile_1y` — regime state
- `cs_setup` / trigger / stop — BAJFINANCE Coiled Spring setup flag
- `quiet7` — compression watchlist

## Notes
- GitHub schedules can drift 5–15 min; harmless here.
- If a run fails (rare feed hiccup), the next day's run fills in normally.
- NSE participant data publishes ~18:00 IST; the 19:15 schedule allows slack.
