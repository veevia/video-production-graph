# VPG Glass Box

A local review room for VPG films: see what needs your decision, watch and compare
versions, inspect the evidence, and return notes to the producer.

The complete app is included here: Python backend, vanilla HTML/CSS/ES modules,
producer CLI, current interface and synthetic tests. No frontend build,
model account, Copilot installation or hosted service is required.

## Run

Requires **Python 3.11+ on macOS or Linux**. The server uses the standard library;
PyYAML is optional. Windows is not supported because reaction storage uses
`fcntl.flock`. Browser playback depends on the browser's installed codecs.

From the Video Production Graph checkout, with its Python environment active:

```sh
python -B apps/glassbox/server.py --root /absolute/path/to/my-films
# Open http://127.0.0.1:4420/
```

`--root` is required and repeatable. A root may be one VPG project or a parent
folder containing projects. Projects are recognized by `vpg.project.yaml`.
The bundled engine is found relative to the app, not the shell's working directory.
When running this app outside the checkout, supply `--engine /path/to/vpg`.

Library indexing is **opt-in**:

```sh
python -B apps/glassbox/server.py --root /absolute/path/to/my-films \
  --library-root /absolute/path/to/videos \
  --library-root /absolute/path/to/archive
```

| Option | Behavior |
|---|---|
| `--root DIR` | Required project discovery root; repeatable |
| `--engine DIR` | Folder containing `graph/workflow.yaml`; defaults to the enclosing VPG checkout |
| `--port N` | Loopback port, default 4420; an occupied port is refused |
| `--data-dir DIR` | Private reaction log and library index; must be outside project, library, engine and UI roots |
| `--library-root DIR` | Explicitly enable video indexing for this folder; repeatable |
| `--no-library` | Explicitly disable indexing, which is also the default |
| `--rescan SECONDS` | Project discovery interval, default 60 seconds |
| `--static-dir DIR` | Alternate UI, relative to this app folder or absolute |
| `--quiet` | Disable HTTP request logging |

The data-directory default remains `~/.copilot/extension-data/vpg-glassbox` for
compatibility with existing installations. It is an ordinary local directory:
Copilot is not used. Set `--data-dir` to any suitable private folder.

**Existing users:** supply the project and library roots you previously used.
Keep project paths and `--data-dir` unchanged to preserve reaction associations;
project and library IDs are path-derived. Installing this copy does not move data
or restart an existing server. Never run tests against your production store.

## What you can do

| View | Capabilities |
|---|---|
| Inbox | Waiting human gates and release decisions; findings to answer; separate informational groups; a Producers line and one line per note with its pickup state |
| Films | Contact sheet or searchable file list; versions, library/archive filters, sorting, copies, other-video collections and rescan |
| Watch | Video and captions, finding/note timeline, key frames, reviews, automatic checks and timecoded reactions |
| Decide | Review the evidence and record approval or requested changes, with confirmation and an explicit engine-boundary warning |
| Compare | Synchronized players, score/verdict changes, per-version runtime targets and the brief's cap, measurement-basis warnings and A/B/tie preference |
| Pipeline | Each node's state, contract, gates, reviews, artifacts, telemetry and governed/direct-run provenance |
| Files | Evidence browsing and media previews; library copies belonging to a job stay in that job's review room |
| Activity | Reactions and producer threads, filters, pickup/resolution/reopening, links to each target, permalinks (`#/activity?r=ID`), Markdown export and copy for chat |

Keyboard: Space plays/pauses; Left/Right seek (Shift for 5 seconds); N adds a note;
G opens Pipeline; C opens Compare; D opens Decide (the waiting decision, else the one you recorded); / searches; ? opens Help;
Esc dismisses a dialog or drawer. Marks use words, shapes and line styles, not
color alone; form fields have borders of at least 3:1, the tab focus ring is drawn
inside the tab bar so it is never clipped, and tap targets on phones are at least
24 px tall. Version frames show a still captured a third of the way into the master
wherever they appear; the poster is the fallback, and Watch's player keeps it. The UI follows [DESIGN.md](DESIGN.md); [PRODUCT.md](PRODUCT.md) records
the capability contract.

## Observe VPG, do not impersonate it

Glassbox reads candidate folders and governed `.vpg/state.json` versions. It loads
the project's selected workflow, including the default 25-node graph and the
27-node quality graph. Unknown workflows display a fallback warning.

Optional telemetry, direct-run records and provisional-approval evidence appear
when present. Missing telemetry does not prevent viewing older projects. Direct
builds remain labelled as such; they do not acquire engine approval by appearing
in this app.

**An Approve reaction is not `vpg approve`.** Reactions are messages to the
producer, stored outside the project. The app never runs nodes, edits controls,
changes thresholds, updates the engine ledger or grants authority. A named human
must still make the engine's required decisions through the governed workflow.
While a gate still waits, or a release remains the latest fully reviewed version,
the owner may replace a recorded reaction with a newer decision; both stay in the log.
Needs you means only the owner: a gate you decided while the governed run still
waits reads "Decided by you · engine waits", with its own drawn ring (a check or a
cross) in the pipeline, gates list, drawer, key and room sentence, and the Inbox no
longer counts it. Each version shows your own verdict beside its status ("You
requested changes · picked up for v29", "You approved") on the rail, contact sheet,
versions table and room sentence; the table's Notes column and the timeline's My
notes lane count your decisions as well as your notes.
The producer's `resolve` means it reported addressing a note, not that a human
accepted the result. Library notes do not enter a job's producer inbox.

## Producer channel

From the repository root:

```sh
python -B apps/glassbox/inbox.py --project /absolute/path/to/my-film
python -B apps/glassbox/inbox.py --project /absolute/path/to/my-film --json
python -B apps/glassbox/inbox.py --project /absolute/path/to/my-film \
  --ack REACTION_ID --reply "The next version will shorten the opening."
python -B apps/glassbox/inbox.py --project /absolute/path/to/my-film \
  --resolve REACTION_ID --version v3 --reply "The opening is two seconds shorter."
python -B apps/glassbox/inbox.py --all
```

Use `--base http://127.0.0.1:PORT` for another port, and `--data-dir DIR` for the
offline fallback when using a non-default store. `--state` accepts `open`,
`picked_up`, `resolved`, `all`, or a comma-separated list. `--producer` labels
pickup and resolution messages. Exit codes: 0 success, 3 no matching notes,
2 error.

When the server is unavailable, listing reads the private log read-only and says
so. Pickup and resolution require the server, which validates and appends them.
An owner may reopen a resolved note; the complete thread remains in the log.

## Local API

JSON errors use `{"error": "..."}`. IDs come from the discovery/library APIs,
not arbitrary filesystem paths.

| Route | Purpose |
|---|---|
| `GET /api/meta` | Configuration, YAML backend, workflow availability and library state |
| `GET /api/projects`, `/api/projects/{pid}` | Projects, versions, graph, ledger summary and telemetry |
| `GET /api/projects/{pid}/candidates/{version}` | Media, checks, reviews, findings, runtime targets and reactions |
| `GET /api/projects/{pid}/nodes`, `/nodes/{node}` | Node summaries and detailed evidence |
| `GET /api/projects/{pid}/gates` | Human, automatic and reviewer gates |
| `GET /api/projects/{pid}/compare?a=...&b=...` | Comparison, score deltas, targets and measurement bases |
| `GET /api/films`, `/api/needs-you` | Contact sheet and actionable/informational inbox |
| `GET /api/library`, `/api/library/films/{id}`, `/api/library/items` | Library inventory, film detail and paginated files |
| `GET /api/inbox`, `/api/inbox/summary` | Producer notes and pickup summary |
| `GET /api/reactions`, `/api/reactions/export` | Reactions and Markdown export |
| `POST /api/reactions`, `/api/reactions/batch` | Validated reactions; batches exclude thread events |
| `POST /api/library/rescan` | Refresh only the configured library roots |

Reaction kinds: `approve`, `request_changes`, `agree`, `disagree`, `note`, `flag`,
`prefer`, `ack`, `retract`, `resolve`, `reopen`. The agent channel
(`X-Actor: agent`) accepts only `ack` and `resolve`. A resolve requires an existing
version and explanation; the owner reopens the resolve with a reason.

Owner decisions in the read API: `owner` on `/api/films` frames and on candidate
detail is the owner's latest approve or request changes on that version (its
release gate or the version itself): `{id, kind, ts, text, version, state, reopened,
producer, picked_for, resolved_in, reply}`. `picked_for` is the first version label
in the producer's pickup text that differs from the decided version; `reply` is the
producer's first sentence. A human gate decided while the governed run waits has
`engine_waits: true` on `/gates` and node status `decided` ("decided by you; engine
waits"); `redecide: true` marks where a newer decision may be recorded. Job rows on
`/api/films` list those gates as `decided`, never as decisions. Candidate detail
adds `script_target_s` when the script names a runtime, and `notes_count` counts the
owner's notes, flags and decisions. `GET /api/inbox/summary?items=1` adds `items`
per job, newest first: `{id, kind, kind_label, state, reopened, target_words,
version, ts, waiting_since, resolved_in, reply, producer, text}` with `text` and
`reply` cut to their first sentence.

The append-only `reactions.jsonl` has UUIDs, UTC timestamps, actor, project,
version/target and text. Writes are locked and fsynced; new files use mode 600.
Unknown fields, invalid targets and disallowed actions are rejected.

## Privacy and local storage

* Bind only to `127.0.0.1`; do not expose or reverse-proxy this single-user app.
  Host/Origin checks, same-origin policies, CSP and path-containment checks are
  defense in depth, not multi-user authentication. Framing is denied.
* No CDN, downloaded font, model call or external service is used. Markdown
  exports contain project information and should be treated as private material.
* Project, library, engine and UI roots are read-only. The server writes only
  the reaction log and atomic library index in the private data directory.
  `library-map.json` in that directory is an optional operator-authored grouping
  override.
* HTTP responses use `Cache-Control: no-store`, **but the browser separately
  persists 240-pixel JPEG thumbnails in IndexedDB** (`glassbox-stills`). This is
  intentional, to avoid repeatedly decoding films. Clear site data for the
  app's loopback origin to remove them; use a temporary browser profile when
  persistent thumbnails are inappropriate. If IndexedDB is unavailable, stills
  remain in memory only.
* Never commit production media, reaction logs, indexes, browser profiles,
  personal preference records or screenshots of confidential films.

## Tests and rollback

From this app directory:

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -B -m pytest -q
python -B tests/ui_capabilities.py
```

The browser check also requires ffmpeg. It creates fictional films and an isolated
server/store in a temporary directory, checks the capability contract, verifies
project bytes are unchanged, and shuts everything down. Node.js is used by the
router regression test; CI supplies it. No paid service or real production is
needed. The bundled-engine contract tests use VPG's normal Python dependencies.

The public bundle ships only the current `static/` interface. Historical UI
snapshots stay in the authoring workspace for local rollback; they are not public
runtime dependencies. Use a reviewed Git version for public rollback, or point
`--static-dir` at an explicitly saved interface. Do not change a running service
merely to inspect a rollback version.

## Limitations and non-goals

* Not an editor, renderer, workflow executor, hosted application or approval
  authority. No engine write-through or embedded Copilot canvas is included.
* Discovery refreshes every 60 seconds and the UI polls every 15 seconds.
  Library files refresh on rescan; missing media is never regenerated.
* Legacy review mapping, film grouping and approval/version matching can be
  heuristic; inspect the linked evidence. Quick duplicate fingerprints are not
  full content hashes. Path changes give files/projects new IDs.
* Comparison warnings distinguish measurement bases and runtime targets. New
  craft-metric trends, pairwise ranking and specialized look/animatic previews
  remain future work; the underlying evidence is accessible as files.
* A decision recorded here stays a message: the engine keeps waiting at its gate
  until a named human approves there. Record a new decision appends a newer
  reaction and the older one stays in the log.
* `picked_for` reads a version label from the producer's free-text plan; a plan
  that names no version shows "picked up" without one.
* The runtime clause ("runs 01:33.1 against the brief's 65 s cap; the script
  targets 90 s") is computed in the browser from the brief's cap, the script target
  and the measured duration; it is absent when any of them is unknown.
* Playback/stills require browser-decodable media. MP4/QuickTime duration is
  read from headers; other containers may have no duration. Nothing is
  transcoded. The pipeline view does not draw every regeneration edge.

Original app code and documentation use the [MIT license](LICENSE).
System fonts are referenced, not bundled. Optional Python/browser tools retain
their own licenses.
