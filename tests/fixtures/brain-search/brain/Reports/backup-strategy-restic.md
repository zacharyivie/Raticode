# Backup strategy with restic

Laptops back up nightly with restic to the NAS, and the NAS copies its restic repository to Backblaze B2 once a week. That is three copies on two kinds of storage with one off site.

## Retention

Keep 7 daily, 4 weekly and 12 monthly snapshots (restic forget --prune). Run restic check monthly and restore one random folder each quarter to prove the backups work.
