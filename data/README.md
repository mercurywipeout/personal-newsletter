# data/

Runtime data directory for the feed scorecard system.

| File / Dir              | Description                                           | Committed? |
|-------------------------|-------------------------------------------------------|------------|
| `feeds.json`            | Per-feed state (status, streak counters, timestamps)  | No         |
| `feed_stats_daily.json` | Aggregated daily metrics per feed                     | No         |
| `runs/YYYY-MM-DD.jsonl` | Append-only per-event telemetry for each run day      | No         |
| `.gitkeep`              | Keeps this directory tracked in git                   | Yes        |

All runtime files are gitignored. Only `.gitkeep` is committed.
