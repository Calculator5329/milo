# Shared preferences

Milo keeps the browser notebook and native WebKit shell in sync through one local settings record. The server constructs `Preferences` with an explicit settings directory. The class stores `preferences.md` there as JSON and uses `DocumentWorkspace` for atomic writes, optimistic concurrency, and retained revisions.

`get()` returns `voice`, `mode`, `sound`, `rate`, `pitch`, `theme`, `model`, and the document `revision`. The defaults are Marius, conversational mode, natural sound, rate 1, neutral pitch, workshop theme, and the quick Gemma 4 model. Records written before `pitch` or `model` existed read back with those defaults.

`update(partial)` accepts any subset of the five preference fields. It validates the complete allowed vocabulary, normalizes `small-speaker` to `small`, merges against the latest stored record, and retries a bounded number of revision conflicts. An empty or identical patch leaves the revision unchanged. Unknown fields are rejected, so API configuration, keys, conversation history, and document state cannot enter this file.

The allowed values are:

- `voice`: `marius`, `javert`, `bill_boerst`, `stuart_bell`, `alba`
- `mode`: `conversational`, `precise`, `brainstorm`
- `sound`: `natural`, `clear`, `small`
- `rate`: `0.96`, `1`, `1.04`
- `theme`: `workshop`, `blueprint`, `hifi`, `library`, `moonroom`
- `model`: any key of `model_catalog.MODELS`, including the unfiltered `gemma3-abliterated:12b-q4`; the corner app and the notebook share the choice

If the settings record is missing, the first read creates the defaults. Existing invalid JSON, invalid UTF-8, missing fields, extra fields, and unsupported values produce an error without replacing the existing bytes. Previous valid contents remain under `.history/preferences.md/` after each change.
