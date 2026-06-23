"""
Daily check-in Google Sheet -> CSV sync (link-based, no auth)
---------------------------------------------------------------
Downloads the daily check-in Google Sheet via its public CSV export URL
and overwrites data/daily_checkin.csv with the current full sheet
contents. Requires the sheet to be shared as "Anyone with the link can
view".

Like the service-account version this replaced, there's no upsert logic:
the sheet IS the full history (you append rows to it directly in Sheets),
so every sync is a full, idempotent overwrite -- re-running it never
duplicates or loses anything. Output is the sheet's raw export, so it
keeps the same double-header shape load_checkin() already expects.

Usage:
    python scripts/sync_checkin.py --sheet-id <SHEET_ID> --gid <GID>
    # or export GOOGLE_SHEET_ID / GOOGLE_SHEET_GID and just run:
    python scripts/sync_checkin.py

SHEET_ID is the long string in the sheet's URL between /d/ and /edit.
GID identifies the specific tab -- the number after #gid= in the URL
(0 if there's only one tab, which is the default here).
"""

import argparse
import os
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_OUT_PATH = DATA_DIR / "daily_checkin.csv"
EXPORT_URL_TEMPLATE = "https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def fetch_csv(sheet_id: str, gid: str) -> bytes:
    """Download the sheet's current contents as raw CSV bytes."""
    url = EXPORT_URL_TEMPLATE.format(sheet_id=sheet_id, gid=gid)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    # A sheet that isn't actually link-shared (or a wrong id/gid) returns
    # Google's HTML login/error page with a 200 status instead of a clean
    # 4xx -- catch that case explicitly rather than silently writing junk.
    content_type = resp.headers.get("Content-Type", "")
    if "text/csv" not in content_type or resp.content.lstrip().startswith(b"<"):
        raise RuntimeError(
            "Response doesn't look like CSV (got an HTML page instead). "
            "Check that the sheet is shared as 'Anyone with the link can view', "
            "and that --sheet-id/--gid are correct."
        )
    return resp.content


def main():
    parser = argparse.ArgumentParser(description="Sync the daily check-in Google Sheet into data/daily_checkin.csv.")
    parser.add_argument(
        "--sheet-id",
        default=os.environ.get("GOOGLE_SHEET_ID"),
        help="Spreadsheet ID (the long string in the sheet's URL between /d/ and /edit).",
    )
    parser.add_argument(
        "--gid",
        default=os.environ.get("GOOGLE_SHEET_GID", "0"),
        help="Tab ID (the number after #gid= in the URL). Defaults to 0 (first tab).",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH, help="Where to write the CSV.")
    args = parser.parse_args()

    if not args.sheet_id:
        raise SystemExit("Missing sheet ID. Pass --sheet-id or set GOOGLE_SHEET_ID.")

    content = fetch_csv(args.sheet_id, args.gid)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(content)
    print(f"Synced {args.out.name} from Google Sheets ({len(content)} bytes).")


if __name__ == "__main__":
    main()
