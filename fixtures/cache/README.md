# fixtures/cache

Committed model-call cache entries. Tests run with `PUNCHWALK_CACHE=readonly` and read here
after the runtime cache (`DATA_DIR/cache`), so recorded calls replay offline.

- Layout: `<key[:2]>/<key>.json` (request description, metadata, JSON response) plus a
  `<key>.txt` sidecar with the raw SAM stream text.
- `files_index.json` maps Meta file ids to content sha256 and repo-relative paths.
- Key: `sha256(provider, endpoint, model, canonical request JSON minus volatile fields,
  sha256 of uploaded bytes)`; see `punchwalk/clients/cache.py`.
- Add entries with `Cache.promote(keys)` (used by `make record`). Never hand-edit them.

Current entries: the Phase 0 probe (`make probe`, synthetic clip in `fixtures/probe/media/`).
