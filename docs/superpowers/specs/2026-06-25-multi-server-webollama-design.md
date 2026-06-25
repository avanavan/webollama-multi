# WebOllama Multi-Server Support — Design Spec

**Date:** 2026-06-25
**Status:** Approved (design)
**Approach:** Light modularization of the existing single-file Flask app (`app.py`) — extract a server registry and a per-server Ollama client, route everything through them.

## Goal

Let one WebOllama instance manage multiple Ollama servers at the same time. Specifically:

1. **Multiple servers**, managed from the web UI (add / edit / remove), persisted to a JSON file on disk.
2. **Synced mutations** — pull, delete, and create broadcast to all servers by default, with a per-action override to narrow the target set.
3. **Reconcile drift** — a merged Models view that shows which models exist on which server, with one-click "fix" actions to make servers match.
4. **Real per-server progress bars** for pull and create (today pull has none; create has a text-only log).
5. **Create a model with a custom context size** (`num_ctx`) plus arbitrary free-form Ollama parameters — currently impossible from the UI.

## Non-goals

- Multi-user / auth / RBAC. Single trusted operator, as today.
- A database. Persistence is a JSON file.
- Backend stream multiplexing. Each server's progress stream is an independent connection (see §6).
- Implementing "create from GGUF/Safetensors file" upload (still out of scope, as in the current app).

## Current state (baseline)

- `app.py` (~672 lines) is a single Flask module. Every Ollama call is an inline `requests` call against two module-level globals: `OLLAMA_API_BASE` and `OLLAMA_API_URL = f"{OLLAMA_API_BASE}/api"`.
- Pull (`/pull`) is non-streaming (`stream: False`) — only a success/failure flash message, **no progress bar**.
- Create (`/create-model`) supports optional streaming with a **text log** (no progress bar); payload sends only `from` / `system` / `template` / `quantize` — **no `num_ctx`**.
- Delete (`/models/delete/<name>`) is a non-streaming DELETE.
- Frontend: Bootstrap 5.3 + Font Awesome, all JS inline in Jinja templates, one `static/css/style.css`. No server selector anywhere.
- CSRF via flask-wtf (`CSRFProtect`, global). Streaming fetches already send `X-CSRFToken`.
- `app.run(host=HOST, port=PORT, debug=True)` — **not** `threaded=True`.
- No tests, no test dependency.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Server config | Managed in the web UI, persisted to a JSON file (`servers.json`). |
| Mutation targeting | Default to **all** enabled servers; per-action override via checkboxes. |
| Reconcile | Yes — merged Models view shows drift with one-click fix. |
| Progress UI | Real per-server progress bars for **both** pull and create. |
| Models view | Merged list with a per-server presence badge per row (doubles as the drift view). |
| Single-server pages | A **global server switcher** in the navbar sets the active server (stored in session). |
| Create params | `num_ctx` field **plus** a free-form key/value parameter editor. |

---

## Architecture

Two new modules; `app.py` stays as the route layer but loses its hardcoded server globals.

### 1. `servers.py` — server registry & persistence

Owns the list of servers and the active-server selection.

**Record shape (in `servers.json`):**
```json
{
  "servers": [
    { "id": "f3a1…", "name": "Local", "base_url": "http://127.0.0.1:11434", "enabled": true }
  ]
}
```

**Public API:**
- `list_servers() -> list[dict]`
- `get_server(id) -> dict | None`
- `get_enabled() -> list[dict]`
- `add_server(name, base_url, enabled=True) -> dict` (generates `id`, validates, dedupes by normalized `base_url`)
- `update_server(id, **fields) -> dict`
- `delete_server(id) -> None`
- `get_active_id(session) -> str` (returns `session['active_server_id']` if it points at an existing enabled server, else the first enabled server's id)
- `set_active(session, id) -> None`

**Persistence safety:**
- All writes guarded by a module-level `threading.Lock` (we enable `threaded=True`).
- Atomic write: serialize to a temp file in the same dir, then `os.replace()` over `servers.json`.
- File path from env `WEBOLLAMA_SERVERS_FILE`, default `servers.json` next to `app.py`.

**Seeding / back-compat (critical):**
- On first load, if `servers.json` does not exist, create it seeded with a single server built from the existing `OLLAMA_API_BASE` env var (name `"Default"`). Existing single-server deployments keep working with zero config change.
- `base_url` is stored **without** the `/api` suffix; the client appends `/api` (matching today's `OLLAMA_API_URL` construction).

### 2. `ollama_client.py` — per-server client

A thin wrapper around the current inline `requests` calls, constructed from a base URL.

```python
class OllamaClient:
    def __init__(self, base_url, timeout=...): ...
    def version(self): ...
    def tags(self): ...
    def show(self, model): ...
    def pull(self, model, stream=False): ...       # returns requests.Response (stream=True for SSE forwarding)
    def delete(self, model): ...
    def create(self, payload, stream=False): ...    # returns requests.Response when stream=True
    def ps(self): ...
    def generate(self, payload, stream=False): ...
    def chat(self, payload, stream=True): ...
    def ping(self) -> bool: ...                      # short-timeout /api/version for health badges
```

- Helper `client_for(server_id_or_record) -> OllamaClient`.
- Replaces all `requests.post(f"{OLLAMA_API_URL}/…")` usages in `app.py`.

### 3. Active server & navbar switcher

- `session['active_server_id']` drives single-server pages: **Chat, Generate, Model detail, Version**.
- Navbar dropdown lists enabled servers + a live status dot; selecting one POSTs to `POST /servers/active` (CSRF-protected) and redirects back.
- Single-server route handlers resolve the client via `client_for(get_active_id(session))`.

### 4. Server management UI — `/servers`

New `templates/servers.html` + sidebar nav entry.

| Route | Method | Purpose |
|---|---|---|
| `/servers` | GET | Table: name, base_url, live status badge (`ping()`), enabled toggle, edit/delete; + "Add server" form. |
| `/servers/add` | POST | Validate (name + base_url required, normalize, dedupe) → `add_server`. |
| `/servers/<id>/edit` | POST | `update_server`. |
| `/servers/<id>/delete` | POST | `delete_server` (block deleting the last server; if it was active, fall back to first enabled). |
| `/servers/active` | POST | `set_active`. |

### 5. Multi-server mutations (broadcast, default-all + override)

Pull / create / delete accept a set of **target server IDs**. Forms render a checkbox per enabled server, all checked by default.

- **Delete** (fast, non-streaming): `POST /models/delete/<name>` accepts `target_ids[]`; the handler loops targets, runs delete on each, and returns a **per-server result summary** (flash messages or a results panel). One server's failure never aborts the others. Default targets = servers that currently have the model.
- **Pull / create** (slow): streamed per server — see §6. The broadcast happens client-side (the frontend opens one stream per selected target).

**Partial-failure semantics (all mutations):** isolate each server; collect `{server, ok, message}` per target; never abort the batch on a single failure; surface every server's outcome in the UI.

### 6. Per-server streaming progress (pull + create)

One streaming connection **per server** — no backend multiplexing. The frontend opens N parallel `fetch` readers and renders one progress bar per server.

**New endpoints:**
- `POST /pull/stream` — body `{server_id, model}` → SSE forwarding Ollama's pull progress JSON (`status`, `digest`, `total`, `completed`), terminated by `{"done": true}` or `{"error": "…"}`.
- `POST /create-model/stream` — body `{server_id, model, from, system, template, quantize, parameters}` → SSE forwarding create progress.

**SSE event contract (per line):** raw Ollama JSON forwarded as `data: {…}\n\n`; a final `data: {"done": true}` on success and `data: {"error": "…"}` on failure (mirrors the current `stream_create_model` format so existing parsing patterns carry over).

**Pull UI (`pull_model.html`):**
- Model name input + a target-server checkbox group (all checked by default).
- On submit, JS opens one `/pull/stream` fetch per checked server and renders a card per server: a determinate Bootstrap progress bar (`% = completed/total`), current `status` text, and a final ✓ / ✗. A summary line when all streams finish, then offer "Back to Models".

**Create UI (`create_model.html`):**
- Replaces the current text-only log with the same per-server card/progress-bar treatment.
- Determinate bar when `total`/`completed` are present; otherwise an animated/striped indeterminate bar showing the latest `status` string.

**Concurrency requirement:** change `app.run(...)` to include `threaded=True` so N simultaneous streams (and other requests) don't serialize.

### 7. Models page: merged view + drift / reconcile

`models.html` + the `/models` handler become the reconcile surface.

- Fetch `tags()` from each enabled server. An unreachable server is rendered as **offline** (its column shows "unknown") and does **not** break the page.
- Merge rows by model name. Each row shows a **presence badge per server** (has it / missing / offline-unknown).
- Rows missing on ≥1 reachable server are flagged as **drift**; a banner at the top shows the drift count.
- Per-row actions:
  - **View** → model detail on the active server (or the first server that has the model).
  - **Update / re-pull** → streaming pull (§6) to the servers that have it (or selected).
  - **Delete** → broadcast delete (§5) to servers that have it (or selected).
  - **Sync** → "Pull to servers missing it" (reuses the §6 streaming UI, targeting only the missing servers) and "Delete everywhere".
- Sorting (name/size/modified) is preserved; size/parameter/quantization columns show values from any server that has the model (note when servers disagree is out of scope — first-found wins, with the presence badges making divergence visible).

### 8. Create model with `num_ctx` + free-form params

- `create_model.html` gains:
  - A **Context size (`num_ctx`)** number input (reuse the pattern already in `generate.html`: min 512, common-value hint).
  - A **key/value parameter editor** (repeatable rows: param name + value, "add row" / "remove row") for arbitrary Ollama parameters.
  - A target-server checkbox group (all checked by default), feeding the §6 streaming flow.
- Backend assembles a `parameters` dict (`num_ctx` merged with the free-form rows; values coerced to int/float/bool where they parse, else string) and includes it in the `/api/create` payload alongside the existing `from` / `system` / `template` / `quantize`.

### 9. Cross-cutting concerns

- **CSRF:** all new POST routes use the existing flask-wtf tokens; streaming endpoints receive `X-CSRFToken` like the current create flow.
- **Health/reachability:** `ping()` drives status badges on `/servers` and the navbar; per-request failures degrade gracefully to "offline".
- **`debug=True` + `threaded=True`:** keep debug; add threaded.
- **Docs:** update `README.md` and `.env.example` — document `servers.json`, `WEBOLLAMA_SERVERS_FILE`, and that `OLLAMA_API_BASE` now only seeds the first server on first run.

## Testing strategy

The repo has no tests today; this adds the first.

- **Dev deps:** add `pytest` and a requests-mocking library (`responses` or `requests-mock`) to a `requirements-dev.txt`.
- **TDD the new modules:**
  - `servers.py`: add/get/update/delete, dedupe, enabled filtering, active-id fallback, atomic write, first-run seeding from `OLLAMA_API_BASE`, lock-guarded writes.
  - `ollama_client.py`: each method hits the right URL/verb/payload with HTTP mocked; `ping()` true/false; streaming methods return the raw response for SSE forwarding.
- **Route tests (Flask test client, client mocked):** server CRUD + active switch; delete broadcast collects per-server results and tolerates one failure; create payload includes assembled `parameters` (incl. `num_ctx`); models page merges presence across servers and survives an offline server; pull/create stream endpoints emit the SSE contract.
- **Manual verification:** run two local Ollama endpoints (or one real + one bogus URL to exercise offline handling); pull a small model to both and watch the two progress bars; create a model with a custom `num_ctx`; confirm drift badges and one-click sync.

## File-change summary

**New:**
- `servers.py`, `ollama_client.py`
- `templates/servers.html`
- `requirements-dev.txt`, `tests/` (test modules)
- `servers.json` is created at runtime (gitignored).

**Modified:**
- `app.py` — remove server globals; route through `servers.py` + `ollama_client.py`; add server-management, active-switch, and `*/stream` routes; broadcast delete; assemble create `parameters`; `threaded=True`.
- `templates/base.html` — navbar server switcher + `/servers` sidebar link.
- `templates/pull_model.html` — target checkboxes + per-server progress bars.
- `templates/create_model.html` — `num_ctx` + free-form params + target checkboxes + per-server progress bars.
- `templates/models.html` — merged presence badges, drift banner, sync actions.
- `static/css/style.css` — progress-card / badge styling as needed.
- `README.md`, `.env.example`, `.gitignore` (add `servers.json`).

## Open implementation notes (resolved defaults)

- Running-models page **merges** all enabled servers (read-only, useful). Model detail / View uses the active server, or the first server that has the model.
- Deleting the **last** server is blocked. Deleting the active server falls back to the first enabled server.
- `base_url` stored normalized (scheme + host + port, no trailing `/api`); client appends `/api`.
