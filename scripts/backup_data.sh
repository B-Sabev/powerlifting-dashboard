#!/bin/bash
# Backs up the two gitignored personal-data files (SQLite DB + check-in CSV) to a
# separate private GitHub repo so they are off-site and versioned.
# Run as the last step of /etc/cron.daily/powerlifting-dashboard-sync.
# The remote uses the `github-pl-backup` SSH host alias (see ~/.ssh/config) which
# selects the deploy key scoped only to that repo.
set -euo pipefail

SRC=/home/borislav/Projects/powerlifting-dashboard/data
DEST=/home/borislav/Projects/powerlifting-data-backup

cp "$SRC/powerlifting.db" "$SRC/daily_checkin.csv" "$DEST/"

cd "$DEST"
git add -A

# No-op if nothing changed since last run (avoids empty commits)
if git diff --cached --quiet; then
    echo "backup_data.sh: no changes since last backup, skipping commit."
else
    git commit -m "backup $(date +%F)"
    git push
    echo "backup_data.sh: backup committed and pushed."
fi
