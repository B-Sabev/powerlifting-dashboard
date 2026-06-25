---
name: run-dashboard
description: Launch this Streamlit dashboard and visually verify it with Playwright screenshots of every tab. Use when asked to run, test, verify, or screenshot the dashboard UI (e.g. after a refactor or feature change), instead of rediscovering the setup from scratch.
---

# Run & verify the powerlifting dashboard

This project has no `chromium-cli` and no Node/Playwright setup — verification
goes through Python Playwright + a committed driver script
(`dev/screenshot_dashboard.py`). Don't re-derive this each session; follow it
verbatim.

## 1. Check Playwright is ready (usually already is — verify before installing)

```bash
.venv/bin/python -c "import playwright" && echo "pip package OK"
ls ~/.cache/ms-playwright 2>/dev/null && echo "chromium binary OK"
```

If either check fails:

```bash
.venv/bin/pip install playwright          # not in requirements.txt by design — kept out of prod deps
.venv/bin/python -m playwright install chromium   # ~290MB, one-time; idempotent if already cached
```

`playwright install chromium` is a no-op if the binary is already present, so it's
safe to run unconditionally — don't skip it "to save time," skip the explanation
checks above instead if you want to save a round-trip.

## 2. Launch the dashboard in the background

```bash
nohup .venv/bin/streamlit run powerlifting_dashboard.py --server.port 8765 --server.headless true \
  > /tmp/streamlit_dashboard.log 2>&1 &
echo $! > /tmp/streamlit_dashboard.pid
timeout 30 bash -c 'until curl -sf http://localhost:8765 >/dev/null; do sleep 1; done'
```

Don't `sleep N` and hope — poll the port. If port 8765 is already taken from a
previous run that wasn't torn down, kill it first: `pkill -f "streamlit run powerlifting_dashboard.py"`.

## 3. Drive it and screenshot every tab

```bash
.venv/bin/python dev/screenshot_dashboard.py --url http://localhost:8765
```

This clicks through every `button[role="tab"]` (works regardless of how many
tabs exist — don't hardcode tab names), screenshots each as full-page PNG into
`dev/screenshots/` (gitignored — these are throwaway, not committed), and exits
non-zero if any browser console/page error fired during the run.

Check the exit code and stderr output first — that alone tells you pass/fail.
**Only `Read()` the PNGs back into context if you need a human-facing visual
check** (e.g. confirming a layout change looks right); for a routine "did this
break anything" pass, the exit code is the cheaper signal and the right one.

## 4. Tear down

```bash
kill $(cat /tmp/streamlit_dashboard.pid) 2>/dev/null
pkill -f "streamlit run powerlifting_dashboard.py" 2>/dev/null
grep -iE "error|exception|traceback" /tmp/streamlit_dashboard.log || echo "no errors in server log"
```

Always stop the server before relaunching, or the next run hits
`Address already in use` on port 8765.

## Notes

- `dev/screenshot_dashboard.py --help` for the full flag list (viewport size,
  output dir, render-wait timing).
- No visual baseline/diffing is set up (skipped by design — overkill for a
  personal project with no CI). Verification is: pytest for the pure math in
  `lib/calculations.py`, this script for "does the UI render without errors."
- If the dashboard requires real data (`data/powerlifting.db`,
  `data/daily_checkin.csv`) and those don't exist locally, tabs will show their
  `st.info`/`st.stop()` placeholders instead of charts — that's expected
  behavior, not a failure.
