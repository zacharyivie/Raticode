# Postgres vacuum tuning

Autovacuum fell behind on the events table, so dead tuples bloated it to three times its live size. Lower autovacuum_vacuum_scale_factor to 0.02 for big tables and raise autovacuum_max_workers. Long-running transactions keep old row versions alive and block cleanup.
