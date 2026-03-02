# data/

Runtime data directory. All files here are gitignored except `.gitkeep`.

| File / Dir                   | Description                                              | Committed? |
|------------------------------|----------------------------------------------------------|------------|
| `feeds.json`                 | Per-feed runtime state (status, streak counters)         | No         |
| `feed_stats_daily.json`      | Aggregated daily metrics per feed                        | No         |
| `feed_recommendations.json`  | Ranked feed candidates from weekly discovery             | No         |
| `runs/YYYY-MM-DD.jsonl`      | Append-only per-event telemetry for each run day         | No         |
| `clusters/YYYY-MM-DD.json`   | Story cluster manifest from each daily run               | No         |
| `cache/`                     | Reserved for future use                                  | No         |
| `.gitkeep`                   | Keeps this directory tracked in git                      | Yes        |

`feeds.json` is bootstrapped from `config/feeds.seed.json` on first run if absent.
