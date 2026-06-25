# Pull Queue, Create-Model Fix, and Navbar Fix — Design

Date: 2026-06-25
Status: Approved

Three issues reported against the multi-server WebOllama:

1. Creating a custom model targeting two Ollama servers returns **HTTP 400**.
2. The navbar **active-server dropdown clips** into the option text.
3. Pulling a model does not survive page navigation, and there is **no way to
   queue** multiple pulls.

## Issue 1 — Create model returns HTTP 400

### Root cause

Ollama's `POST /api/create` returns **400 when the `from` field is empty/missing**
(verified against Ollama 0.30.10: a payload with `from` set returns 200 and
streams; without `from` it returns `400`). The app sends an empty `from` whenever
the Base Model `<select>` rendered as `No models available` (value `""`).

The Base Model list is populated **only from the active server**
(`active_client().tags()` in `create_model_page`). On a fresh or unreachable
active server the list is empty, so the user either cannot submit (the `required`
empty `<select>` fails HTML5 validation) or submits an empty `from` and gets 400.
This is the multi-server failure mode: you cannot create a custom model when the
active server happens to be the empty one, even though another server has models
or the base model is a pullable registry model.

### Fix

- **Union base-model list.** Populate Base Model from the deduped union of every
  *enabled* server's `tags()`, not just the active server. Offline servers are
  skipped silently (same pattern as `merged_models`).
- **Combobox input.** Replace the `<select required>` with an
  `<input list="base-model-list" required>` backed by a `<datalist>` of the union
  models. The user can pick a known model **or type any model name** (e.g.
  `llama3.2`), which each target server auto-pulls if missing (verified: a target
  lacking the base model completes by pulling it from the registry).
- **Backend guard.** In `/create-model/stream`, when the request is a from-model
  create with an empty `from`, return `400 {"error": "Select or enter a base
  model"}`. The frontend already renders `j.error`.

## Issue 2 — Navbar active-server dropdown clipping

### Root cause

`static/css/style.css` globally overrides
`.form-control, .form-select { padding: 0.5rem 0.75rem; }`. This wipes the
right-padding Bootstrap reserves for the `<select>` chevron, so the chevron
background paints on top of the option text. It is most visible in the navbar,
where the select is content-width and short (`Local⌄A`).

### Fix

Restore chevron room and constrain the navbar select width in `style.css`:

```css
.form-select { padding-right: 2.25rem; background-position: right 0.75rem center; }
.form-select-sm { padding-right: 1.75rem; }
.navbar .form-select-sm { min-width: 9rem; max-width: 16rem; }
```

## Issue 3 — Server-side pull queue with resume

Browser-driven pulls cannot resume across navigation: the `fetch` abort cancels
Ollama's download (partial blobs are kept, but the stream stops). WebOllama must
own the pull so it continues regardless of which page the browser is on.

### `pull_jobs.py` — in-memory job registry

Thread-safe (`threading.RLock`). One **job** per (server, model):

```
{ id, server_id, server_name, model, state, total, completed, status, error, seq }
state ∈ { queued, running, success, error, canceled }
```

- **Per-server FIFO worker.** Each server has a `queue.Queue` and one lazily
  spawned daemon worker thread. A server runs **one pull at a time**; different
  servers run **in parallel**.
- **Worker loop.** Pop job → `running` → `OllamaClient(base_url).pull(model,
  stream=True)` → per streamed line, update `total`/`completed`/`status` under the
  lock → `success`. Non-200 or exception → `error` with message. Each line checks
  the job's `cancel` `threading.Event`; if set, close the response and mark
  `canceled`.
- **Dedup.** Enqueuing a model already `queued`/`running` on the same server is a
  no-op (returns the existing job).
- **API.** `enqueue(server, model) -> job | None`, `snapshot() -> [job…]` (sorted
  by `seq`, deep-copied, no internal `Event`), `cancel(job_id) -> bool`,
  `clear_finished()`.
- `seq` is a monotonic counter for stable ordering.

`pull_jobs.py` imports `ollama_client` (no circular dependency).

### Routes (app.py) — replaces `/pull/stream`

- `POST /pull/enqueue` `{model, server_ids: [...]}` → one job per server; returns
  `{jobs: [...]}`. Defaults to all enabled servers if `server_ids` omitted.
- `GET /pull/jobs` → `{jobs: snapshot()}`.
- `POST /pull/cancel/<job_id>` → `{ok: bool}`.
- `POST /pull/clear` → clears finished jobs.

All POST routes keep CSRF (frontend sends `X-CSRFToken`).

### Frontend — `pull_model.html` rewrite

- Submit button becomes **"Add to queue"** → `POST /pull/enqueue` with the typed
  model and checked target servers.
- A **Pull queue** section **polls `GET /pull/jobs` every 1s** and renders one card
  per job: server name, model, progress bar (`completed/total`), latest status,
  state badge, and **Cancel** (queued/running) or **Clear finished**.
- On `DOMContentLoaded` it fetches `/pull/jobs` immediately and starts polling —
  **this is the resume**: navigate away mid-pull, return, and the live queue is
  rendered from the registry.
- Model and server names are escaped with `escapeHtml` (XSS).
- Prefill (`?model=`, `?targets=`) and the Models-page **Sync** flow are preserved:
  they prefill the model field and target checkboxes; the user clicks Add to queue.

### Persistence & scope

- **In-memory** registry: survives navigation; resets on app restart (Ollama's
  partial blobs let a re-pull resume). No disk persistence in v1.
- The synchronous `update_model` re-pull is unchanged.
- No global navbar pull indicator in v1 (possible follow-up); the queue lives on
  the Pull page.

## Testing

- `pull_jobs` unit tests with a fake client: enqueue creates a job; dedup; snapshot
  ordering; cancel a queued job; cancel a running job; progress updates from
  streamed lines; error on non-200/exception; sequential-per-server ordering;
  parallel across servers. Workers are synchronized in tests via `Event`s so
  assertions are deterministic.
- Route tests (CSRF disabled, as in existing tests): `/pull/enqueue` returns jobs;
  `/pull/jobs` returns the snapshot; `/pull/cancel`; empty `from` create returns a
  clear 400.
- Remove the old `/pull/stream` forwarding test; keep create-stream tests.
