# server/ - the randomizer website

The FastAPI app for the randomizer site. Architecture, data model, routes and the ordered
work items are in `docs/randomizer_devplan.md`; this file holds the conventions for code
in this package.

## Layering

- `server/` imports from `golf/` and never from `tools/`. Generation, builds and anything
  a test or CLI would also want (such as the vanilla ROM list in
  `golf/randomizer/roms.py`) belongs in `golf/`, with no web dependency.
- `tools/site.py` (`golf-site`) is only a uvicorn launcher.

## App

- `server/app.py` has `create_app(config)`, a factory. Routes are defined inside it, except
  the admin pages' (see "Admin" below), and
  shared objects live on `app.state`: `config`, `strings` and `rate_limiter`, and `db`,
  `builder` and `timings` once the lifespan has started. Nothing is module-level state, so each test
  builds its own app.
- `server/builder.py`'s `SeedBuilder` is the only thing a route calls to generate, build or
  finish. It holds the catalog and curation the seed page also reads. The lifespan calls
  its `warm`, which reads the server's ROM and builds what every seed shares (each par's
  layouts, the signpost banner), so the first seed after a restart is as quick as the
  rest; a missing ROM is logged and the site starts anyway, refusing builds. `finish`
  takes credentials for a signed-in download and none for a guest. Builds run in the threadpool (`run_in_threadpool`), never on the event
  loop.
- Route helpers with no web types live beside the app: `server/forms.py` (the generate
  form to `Settings`, the download form to `PlayerOptions` and ROM hashes),
  `server/views.py` (what a page shows, as dataclasses, and the download file name) and
  `server/ratelimit.py`.
- `server/auth.py` holds sign-in: `DiscordClient` (the two OAuth2 calls), `safe_next` for
  return paths, and `current_user(request)`, the one way a route gets the signed-in
  `User`. Templates get `user`, `sign_in_enabled` and `return_path` from the context
  processor in `create_app`. The session cookie holds only `users.id`, plus the OAuth
  state and return path while a Discord sign-in is under way. `app.state.discord` is the
  client, or None when Discord is not configured.
- `server/live.py`'s `LiveServer` serves an app on a free localhost port for tools and
  tests that drive a real browser.
- A route a script fetches answers a refusal as JSON, `{"error": reason, "values": {...}}`,
  and the script picks the notice for `error`. A missing seed on a path ending `.json` or
  `.ips` is a JSON 404 rather than the not-found page.
- An unhandled exception renders `server_error.html` with a 500, showing the request id so
  a player can quote it; on a path ending `.json` or `.ips` it stays Starlette's plain
  text. The timing middleware logs the traceback before the handler runs, and if the page
  itself fails to render, as it would with the database down, the plain text goes out
  instead.
- `create_app`'s timing middleware is added last, so it wraps every other middleware and
  times the whole server. It mints the request id, puts a `Sample` on
  `request.state.sample` and hands it to `app.state.timings` when the response is done.
  A route that can see a phase from the inside times it with `sample.phase("name")` and
  names what the request came to with the local `outcome(request, reason)`; the reason is
  the same short string the refusal already uses. `SeedBuilder.build` takes the sample so
  the wait for the build semaphore is timed apart from the build.
- The footer on every page shows the site's release, which `create_app` reads once from
  the checkout with `git describe` (`server/version.py`, ADR 0005): `v1.0.2` on a deployed
  tag, `v1.0.2-10-gc66d426-dirty` in development, nothing when git can't say. Tests pass
  `version=` to fix it.
- Configuration is `server/config.py`, read from `GOLF_`-prefixed environment variables.
  A new setting is a field there, a variable in the README's "Running the site" list, and
  a line in the devplan's configuration paragraph.

## Database

- `server/db.py` keeps one `sqlite3` connection in autocommit mode behind a lock. Every
  read or write goes through `db.transaction()`, which holds the lock for its duration:
  keep work inside it short, and never build a ROM while holding it.
- The schema is `server/migrations.py`, ordered SQL scripts applied by `PRAGMA
  user_version`. Migration 1 is the frozen version 1.0 baseline. A committed script is
  never edited; a schema change appends a script. The baseline sets SQLite's
  `application_id` to `GOLF`, and startup rejects unmarked pre-baseline databases.
- `server/ids.py` holds the base62 alphabet and codec every public id shares. A seed's is
  the encoding of its `qr_seed_id`; a round's permalink id is drawn as text.
- `server/seeds.py` is the only code that writes `seeds` and `seed_holes`, and the only
  place seed ids are drawn or converted. It owns withdrawal and restoration: each changes
  only `withdrawn_at` and records an audit action in the same transaction. The seed's
  manifest and unfinished IPS are never changed. The row repeats the manifest's build and
  finish-ABI versions for operations, and loading verifies that the copies agree.
- `server/users.py` is the only code that writes `users`, and the only place player ids
  are drawn. `seeds.creator_id` holds a `users.id`.
- `server/entries.py` is the only code that writes `entries`, and the only place MAC keys
  are drawn. `Entry.keys` stays out of `repr`; keys never go in a page, a log or a manifest.
- `server/timings.py` is the only code that writes `timings` and `timing_day`. A request's
  sample is buffered in memory and written in batches by a flush task the lifespan starts,
  never on the request path; flushing takes the database lock, so it runs through
  `run_in_threadpool`. The first successful sweep of each day rolls all complete days up
  into `timing_day`, in batches of `ROLLUP_DAYS`, before pruning raw samples past
  `RETENTION_DAYS`; a partial rollup must not be followed by a prune. The raw timing row
  keeps the request ID from `X-Request-Id` for lookup beside its log lines. The metrics
  page's raw window cannot exceed retention; its daily trend shows the last `TREND_DAYS`
  even though the rollup is kept for good. The windows and slow-request threshold are
  constants, not configuration, like `GENERATE_CAPACITY` in
  `server/ratelimit.py`. Never `VACUUM` this database: the freed pages are meant to stay
  on the freelist, and a vacuum rewrites every page and makes Litestream ship the lot.
- `server/audit.py` is the only code that writes `admin_actions`, the admin audit log. Its
  `record` takes the caller's connection, so an action and its log row commit together. A
  new admin action adds its name there and records a row; nothing needs a migration, and no
  table carries a "who did this" column of its own.
- `server/submissions.py` is the only place a scan is decoded and verified. It writes
  nothing itself: an accepted scan becomes a round through `server/rounds.py`, and a
  rejected one is stored nowhere. Its rejection reasons deliberately do not say which lookup
  or check failed; the exact cause goes only to the log, at WARNING, through
  `logging.getLogger(__name__)`.
- `server/rounds.py` is the only code that writes `rounds`, `round_holes` and
  `voided_rounds`, and the only place a round's `public_id` is drawn. That id names the
  round everywhere, from its `/r/<id>` permalink to the admin pages and the audit log, and
  moves with it into `voided_rounds` and back on a restore. The row id stays inside the
  module, since a void and restore changes it.

## Admin

- The `/admin` pages admit the signed-in users `Config.admin_users` (`GOLF_ADMIN_USERS`)
  lists. `server/admin_routes.py`'s `admin_router(templates)` builds their `APIRouter`, which
  `create_app` includes, and every route sits behind `require_admin`, a 404 for anyone else.
  No page links to them.
- `server/admin.py` holds the admin pages' reads and view dataclasses, with no web types.
  When it grows, split it into a package by area (seeds, rounds, users, the audit log).
  Admin actions write through the owning module: `flag_round`, `unflag_round`,
  `void_round` and `restore_round` in `server/rounds.py`, which take the round's
  `public_id`. Each takes the admin's `users.id` and logs itself through
  `server/audit.py`.
- Who acted, and a round's history, are read from the audit log, never from a column. A
  round's log rows are keyed by its `public_id`, so its history is one list across a void
  and a restore.
- Actions are POST forms that redirect back with `?result=`, which the page shows. State
  never changes on a GET.
- Withdrawing a seed keeps its public page, manifest, entries and rounds, replaces the
  download form with a public notice and makes the IPS POST answer JSON 410. The admin's
  note stays in the audit log and never reaches a public page. Restoring re-enables the
  same stored IPS.
- Templates live in `server/templates/admin/`, extend `base.html`, and share the macros in
  `server/templates/admin/_admin.html`.

## Pages

- Server-rendered Jinja2 in `server/templates/`, extending `base.html`, styled with the
  vendored Pico CSS green theme (`server/static/VENDORED.md`). Pass `page` in the context
  for the nav highlight. Link static files with `static_url("path")`, never a literal
  `/static/` path (a test checks): `server/static_files.py` adds a hash of the file's
  contents as `?v=`, and serves a versioned URL as immutable. What carries no version,
  such as the rangefinder's module imports and its renders at `/rangefinder-data/`
  (`Config.rangefinder_dir`), is served `no-cache`, so a browser revalidates it.
- Pico's file is minified onto one line; to read it, pretty-print it into the scratchpad
  with `uv run golf-biome format --stdin-file-path=pico.css < server/static/pico.green.min.css`.
- `server/static/site.css` holds only rules Pico has no class for, and takes its colors
  from Pico's variables (`--pico-ins-color`, `--pico-del-color` and the like) so dark mode
  follows. Components that change look with script state carry a `data-state` attribute
  the stylesheet selects on, rather than the script toggling styles or `hidden`.
- No English in templates or scripts: see "Player-facing text" below.
- A page that shows one of several messages, such as the generate form's refusal notices,
  picks each in an `if` chain calling `t()` with its literal key, never a key built from
  a variable, so the strings test can find every key.
- Document-like pages live as Markdown directly under `server/content/pages/`. Their file
  name is the `/pages/<slug>` path and must contain only lowercase letters, digits and
  hyphens. TOML frontmatter between `+++` lines holds a required `title`, and optional
  `nav_title`, `order`, `enabled` and `listed` fields. Both flags default true. An enabled,
  unlisted page remains available by its URL for review but is not access-controlled;
  disabled pages answer 404. `server/pages.py` validates and renders the catalog once at
  app creation, with raw HTML disabled. `golf-site-new-page <title>` creates a valid stub
  with the frontmatter defaults written explicitly; `--slug` overrides its derived file
  name, and it refuses to overwrite a page. `golf-site --reload` watches the Markdown
  files.
- A directory directly under `server/content/pages/` is instead a collection page, one
  card per entry (ADR 0006). Its name follows the same slug rule. Its `_index.md` holds
  the page frontmatter and an optional intro shown above the cards; every other `.md` file
  is an entry with a required `title`, a required TOML local `date` (`date = 2026-10-01`,
  not a datetime) and an optional `enabled`. An entry's file name is its card's anchor
  id, and its title links there. Entries show newest date first, ties broken by file name
  descending; disabled entries are validated but never rendered. A collection holds no
  directories, and a page file cannot share a collection's name. The page around the
  cards is not an `<article>`, so the cards never nest in one. `golf-site-new-page --entry
  <page> <title>` creates an entry dated today. The date renders through the
  `calendar_date` filter (`server/views.py`), which `localtime.js` formats in UTC.
- `base.html` lists enabled, listed Markdown pages in its document-page dropdown. The
  dropdown is absent when there are none. A Markdown page supplies its own `title` and
  body; its template supplies the top-level heading, so its body starts below h1, and an
  entry's body starts below its card's h2.
- JavaScript only where the browser must act: hashing and storing ROMs
  (`server/static/rom.js`) and fetching and applying a seed's IPS
  (`server/static/download.js`), and showing timestamps and dates in the viewer's time
  zone and locale (`server/static/localtime.js`, below). The first two load `server/static/romstore.js` first, which holds
  the ROM store and `makeT`. Plain scripts, no build step, no frameworks. Everything else is
  a form or a link.
  The one exception is `round.html`'s inline `history.replaceState` line, which drops
  the `?recorded` marker `/s/` redirects with once the page has shown its confirmation, so
  a reload or a copied link is the plain permalink. It carries no English and no state, and
  without it the page still renders correctly with the marker visible.
- Timestamps are stored as UTC ISO 8601 text and shown through the `timestamp` filter
  (`server/views.py`), never printed raw. It renders a `<time>` element holding the UTC
  minute, and `server/static/localtime.js`, which `base.html` loads on every page, rewrites
  its text with `Intl.DateTimeFormat` into the viewer's time zone and locale, keeping the
  UTC text as the title. A timestamp passed into `t()` goes through the filter too; its
  `Markup` passes through unescaped.
- The ROM store is IndexedDB database `golf-randomizer`, object store `roms`, records
  `{id, sha1, bytes}` keyed by catalog ROM id. It holds only bytes whose SHA-1 matched: `rom.js`
  retries a mismatched file with the catalog ROM's iNES header (`golf/randomizer/roms.py`)
  and stores the result when that matches.
- A downloaded ROM is named `notgr_par<par>_<id>.nes` by `download_stem` in
  `server/views.py`, and reaches the script as a data attribute. A file name is data, never
  a strings entry.

## Logging

- `server/logging.py` holds the one configuration: JSON lines on stderr, one object per
  record, with `ts`, `level`, `logger`, `msg`, `request_id` and whatever `extra=` carried.
  `tools/site.py` hands `log_config(config.log_level)` to uvicorn rather than applying it,
  because uvicorn applies it in every worker including the child `--reload` spawns, and
  because `create_app` must not have each test's app fight over the root logger.
- uvicorn's access log is off: Caddy logs every request with the duration the client
  waited (`deploy/Caddyfile`), and the app logs only the notable ones - a 4xx or 5xx, an
  unhandled exception, or a request over `SLOW_MS` - with the phases the proxy cannot see.
- A module logs through `logging.getLogger(__name__)`. An exception caught and turned into
  a user-facing response is logged when the server is the problem: ERROR for a
  `BuilderUnavailableError`, WARNING for a `GenerationError` or a `DiscordError`. A
  `FormError` is not logged - a user's mistake is counted through the sample's outcome.
- Keys, secrets and session contents never reach a record.
  `tests/unit/test_server_app.py` checks that a rejected scan logs its cause and not the
  entry's MAC keys.

## Player-facing text

A human composes every English word a player sees, and Claude
writes none of it, not even as a draft to be rewritten.

The admin pages are the one exception: only admins see them, so their English is written in
`server/templates/admin/` directly, and they call no `t()`. A string an admin action puts on
a public page, such as the flagged marker, is still a catalog key.

The checked-in Markdown pages under `server/content/pages/` are the other exception: their
frontmatter titles and bodies are human-authored editorial content, not interface strings.
Claude may build the machinery around them but does not compose or edit that content. Shared
interface text surrounding them, including the navigation dropdown label, remains in the
strings catalog.

- Apart from the Markdown editorial content described above, every visible string,
  including tab titles, nav labels, button labels, accessible names and script status
  messages, comes from `server/strings/` by key. No English goes in a template or script.
  Proper nouns and data are not strings: ROM titles, hole ids, magic words.
- The catalog is the TOML files under `server/strings/`: `common.toml` for the elements on
  every page (`base.html`), and one file per template named for it - `home.toml`,
  `rom.toml`, `generate.toml`, `seed.toml`, `me.toml`, `not_found.toml`, `server_error.toml`, `sign_in_failed.toml`,
  `round.toml`, `scan_rejected.toml`, `round_voided.toml`. Every file under the
  directory is loaded and merged, subdirectories included. Entries carry their full dotted
  key (`[home.about]`), so a file name is organization only and a key still greps to its
  entry. A top-level namespace lives in exactly one file, and a new template-backed page's
  strings arrive as a new file.
- An entry is a `note` and a `text`. Claude adds keys and notes and never writes or edits
  `text`. Claude always adds an empty `text` field to entries.
  A note is terse fragments of what the string has to get across and the values it
  receives, never wording that could be kept. Notes starting `plain:` mark strings that
  take no HTML.
- Templates call `t("key", name=value)`, whose text may hold inline HTML with values
  escaped, or `t_plain(...)` for tab titles, attributes and anything else HTML would break.
  Empty text renders as a marked placeholder showing the key, with the note on hover.
- A script gets its entries as JSON: the route passes `strings.for_script(prefix)` and the
  template embeds it in a `<script type="application/json">` element. Each page script's
  prefix is a constant in `server/app.py` (`ROM_SCRIPT_STRINGS`, `DOWNLOAD_SCRIPT_STRINGS`),
  and a new script is added to the strings test's list of scripts. The script's `t()`,
  from `makeT` with the element's id, reads it, and calls it with literal keys so the tests
  can find them. Script strings may hold inline HTML like any other: `t()` escapes the values it inserts and
  returns HTML, which the script sets with `innerHTML`.
- `tests/unit/test_server_strings.py` checks that every key a template or script uses is
  in the catalog, that every entry is used, and that the admin templates use none.
- `golf-site-strings` (`tools/site_strings.py`) lists the entries whose text is still empty,
  by file; `--notes` adds what each has to say.
- `golf-site --reload` restarts on changes to the catalog, since the app loads it once.

## Seeing pages

After changing a template, `site.css` or a page script, render the pages and look at the
PNGs before reporting the change done:

```bash
uv run golf-site-screenshot / /rom /generate --generate --expand -o <scratchpad>/shots \
  --rom nes_open_us=nes_open_us.nes --rom mario_open_jp=mario_open_jp.nes
```

Once a Markdown page exists, pass its route as another path (for example,
`/pages/<slug>`) to capture its content and the document-page dropdown.

It serves the app on an in-memory database, captures each page at desktop and phone
widths in light and dark, and exits 1 on a browser console error or a failed request. With
`--rom`, the ROM setup page is captured again after loading those files, and the final card
states are printed. Pass a patched ROM to capture the mismatch state. With `--generate`,
the generate form is submitted and the seed page it lands on is captured as
`<name>-seed.png`, with its download form's state printed; that builds a real seed from the
ROM in `GOLF_ROM_DIR`. With `--rom` too, the ROMs are loaded before generating, so the
download form captures ready rather than missing. With `--login NAME`, each browser signs
in through the development bypass first, so the header captures signed in, and `NAME` is
an admin, so `/admin` pages capture too. With `--expand`, every capture whose page body
has collapsed `<details>` sections (the generate form's club rules, the seed page's rounds,
hole table and details) is taken again with them all open, as `<name>-expanded.png` or
`<name>-seed-expanded.png`. The tool is
`tools/site_screenshot.py`; `tests/integration/test_site_screenshot.py` skips without a
Playwright browser.

## Tests

`tests/unit/test_server_*.py`. Build the app with `create_app(Config(database=":memory:"))`
and use `TestClient` as a context manager so the lifespan opens and migrates the
database. Database tests use `Database(":memory:")` directly.

Posting to `/generate` builds a ROM, so unit tests pass `builder=` a `SeedBuilder` subclass
whose `build` returns a fixed blob, and `rate_limiter=` a small `RateLimiter` to test
refusals (`tests/unit/test_server_app.py`). `tests/integration/test_server_generate_rom.py`
runs the real builder. Posting to `/h/<id>/patch.ips` finishes a ROM, so the same
subclass overrides `finish`, recording the credentials it was given; `tests/integration/test_server_download_rom.py` and
`tests/integration/test_site_download.py` run the real one, the second in a browser.

A scan's path is built from `RoundPayload` and the entry's stored keys (`scan_path` in
`tests/unit/test_server_app.py`); `tests/integration/test_server_submission_rom.py` builds
it instead by running a downloaded ROM's QR routine in the simulator. `/s/` answers a 303,
and `TestClient` follows redirects unless a test passes `follow_redirects=False`, so a test
that cares about the redirect itself says so.

Sign-in tests build the app with `Config(dev_login=True)` and sign in with
`/auth/login?as=<name>`, or with Discord credentials and `discord=` a `DiscordClient`
subclass whose `identify` returns a fixed identity (`FakeDiscord` in
`tests/unit/test_server_app.py`). `tests/unit/test_server_auth.py` runs the real client
against `httpx2.MockTransport`.

`create_app` takes `timings=` a `TimingSink`; one built with `flush_seconds=None` is
flushed by hand, so no background task runs during a test. Tests that read what was
recorded call `app_state(client).timings.flush()` first, since a sample is only buffered
until then. `tests/unit/test_server_timings.py` winds the sink's `now` by hand the way
`test_server_ratelimit.py` winds the rate limiter's `clock`.

Admin tests (`tests/unit/test_server_admin.py`) build the app with `dev_login=True` and
`admin_users={"dev:admin"}`, and sign in as `admin`.

A test that checks *which* refusal notice a page shows names it by string key and builds the
app with `strings=UNWRITTEN`, a catalog with nothing written, in which every string renders
as the placeholder naming its key and the values passed to it. The assertion then holds
whatever the catalog says.
