# Milo freshness

The ingest keeps a rolling 14-day SQLite FTS5 index beside Milo's offline
library. It uses the bundled `feeds.json`, and a failed source does not stop
the rest of the run.

Optional settings live at `~/.local/state/milo/freshness.json`:

```json
{
  "lat": 0.0,
  "lon": 0.0,
  "disabled_feeds": ["Example feed name"]
}
```

The coordinates intentionally default to the placeholder `0,0`. The owner
sets the actual location. `MILO_FRESHNESS_DB` overrides the default database
path, `~/.local/state/milo/freshness.db`.

From `audio/milo`, run:

```sh
python3 -m freshness ingest
python3 -m freshness search "query"
python3 -m freshness status
```

The service and timer files are proposals only. The owner installs and enables
them when ready.
