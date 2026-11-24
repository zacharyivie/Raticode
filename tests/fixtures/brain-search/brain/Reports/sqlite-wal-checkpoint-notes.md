# SQLite WAL checkpoint notes

In WAL mode readers never block the writer. The WAL file grows until a checkpoint copies pages back to the database; automatic checkpoints run at 1000 pages. A long-running read transaction stops checkpoints from completing, so the WAL grows without bound. PRAGMA wal_checkpoint(TRUNCATE) resets it.
