# Milo freshness index

The optional freshness job fetches the feeds in `feeds.json` and keeps a rolling
14-day SQLite FTS5 index. A failed source does not stop the rest of the run.
The index stays local at `~/.local/state/milo/freshness.db`, unless
`freshness_db` or `MILO_FRESHNESS_DB` selects another path.

From the repository root, run one ingest with:

```sh
python milo.py freshness
```

The command is safe to repeat. Milo uses this index for questions such as
“what's in the news” and “anything new lately”. It does not turn the index into
a general web search. The feed requests leave the machine only during ingest.

The module also has direct commands when working from the package directory:

```sh
python3 -m freshness ingest
python3 -m freshness search "query"
python3 -m freshness status
```

The included `milo-freshness.service` and `milo-freshness.timer` are user-unit
templates. They use `WorkingDirectory=%h/milo/milo`. If the repository is
somewhere else, edit that path and the Python interpreter path before enabling
the timer. Install them under `~/.config/systemd/user/`, then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now milo-freshness.timer
```

On Windows, create a daily Task Scheduler task whose action is the repository's
`.venv\Scripts\python.exe milo.py freshness`, with the repository root as its
working directory. The index feature is cross-platform; the supplied timer is
Linux only.
