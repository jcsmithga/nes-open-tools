# Hosted ROMs: Development Plan

> **Note**: This document was written by Claude. It is the plan for letting a host build
> a seed's ROM for other players, who claim its player slots from their phones, so their
> rounds are credited to their own accounts.

## Goal

At an in-person event, players with a NES and an Everdrive or a MiSTer often bring no
laptop, and one person rolls and copies seeds for everyone. Today that person can only
give them a ROM carrying their own credentials or a guest ROM, and either way the
players' rounds are not recorded as theirs. A hosted ROM fixes that: a host opens a
hosting page for a seed, each player scans a QR code on it with their phone and confirms
on the site, and the host downloads a ROM whose player 1 and player 2 positions carry
the two claimants' credentials. One seed is typically hosted many times in an evening,
once per pair.

In scope:

- Hosts: a role beside admin, granted by configuration, that can host and do nothing
  else. Admins are hosts too.
- Hosting: creating a hosted ROM, claiming its slots, releasing a slot, downloading the
  ROM.
- Crediting a round by the key that signed it, so a player's own round is the same record
  whichever controller they played from.
- The data model and claim route built so that the same claim can later serve the general
  player 2 question (`docs/randomizer_devplan.md`, "Player 2's account"): a downloader
  holding slot 0 and inviting a teammate into slot 1.

Out of scope:

- Opening hosting to every player.
- Per-slot bags (see "Club rules").
- Patching player 2's name. The ROM holds one name, player 1's; the game names player 2
  itself.
- A link to `/host` in the site header. Hosts bookmark it, as admins do `/admin`.

## Terms

- **Host**: a signed-in user `Config.host_users` (`GOLF_HOST_USERS`) or
  `Config.admin_users` lists. A hosted ROM's host is the one who created it.
- **Position**: one of a ROM's two players, 0 (player 1) and 1 (player 2): the controller
  a round is played from. The payload's `player_slot` names it, and each position carries
  its own player ID and MAC key (`QrCredentials`, `golf/core/patches/qr_credentials.py`).
- **Round slot**: the `rounds.slot` column, which says whose round it is on its entry:
  0 for the entry's owner, 1 for a teammate who played player 2 on the owner's ROM. On an
  ordinary ROM the two agree; on a hosted ROM a slot 1 claimant plays position 1 and
  records into round slot 0.
- **Hosted ROM**: a seed's ROM a host builds for the players who claim it. It is a
  record on the server until downloaded, and can be downloaded again. A seed can have
  any number of them.
- **Claim**: a signed-in player taking the lowest free slot of a hosted ROM by opening
  its claim link and confirming. Slot 0 is player 1, slot 1 player 2.
- **Claim link**: `/claim/<token>`, one per hosted ROM, shown on the hosting page as a QR
  code while a slot is free.

## Crediting rounds by key

An entry (`server/entries.py`) is one per (seed, user) and holds two MAC keys,
`key_slot0` and `key_slot1`. Today `submit_scan` (`server/submissions.py`) finds the
entry by (seed ID, player ID), verifies with the key matching the payload's position,
and records the round in that slot. So a round's slot is its position: slot 0 is the
downloader's own round, slot 1 is whoever played player 2 on their ROM.

A hosted ROM's player 2 is a claimant playing their own round from position 1. Signed
with their `key_slot1`, it would land in their entry's teammate slot, indistinguishable
from a teammate's round on their own ROM. Instead:

- **A hosted ROM signs each claimant's rounds with their `key_slot0`**, in whichever
  position they hold.
- **`submit_scan` tries both keys for a position 1 payload**: `key_slot1` first, recorded
  in round slot 1 as today; then `key_slot0`, recorded in round slot 0. A position 0
  payload verifies only under `key_slot0`, as today.

The key that verifies says whose round it is, because the server puts a player's
`key_slot0` into a ROM only in a position that player holds. So round slot 0 is always
the entry owner's own round, from an ordinary download or a hosted ROM in either
position, and stats count round slot 0 alone. A player with two scans aiming at round
slot 0 on one seed (their own ROM and a hosted one, or two hosted ROMs of the seed) has
the first kept by the existing first-submission rule.

The stored payload keeps the position byte as the ROM wrote it, so a hosted player 2's
round has position 1 in its payload and slot 0 in its row. Code reads whose round it is
from the `rounds.slot` column, never from the payload; the module docstrings of
`server/rounds.py` and `server/submissions.py` say so. Nothing reads `player_slot` after
the scan today: `_insert_round` and the voided-rounds listing parse the payload for
totals and holes only, and restoring a voided round uses the stored slot.

Trying a second key doubles a forger's odds against a 32-bit MAC, from 2⁻³² to 2⁻³¹ per
scan, behind the scan rate limit. Ordinary ROMs are unchanged.

## Decisions

- **Hosts are configured, like admins.** `Config.host_users` reads `GOLF_HOST_USERS`
  the way `admin_users` reads `GOLF_ADMIN_USERS`, with the same `dev:<name>` rule under
  the login bypass; `Config.is_host` is true for either list. Hosting lives under
  `/host`, behind `require_host` (a 404 for anyone else), so a host who is not an admin
  sees none of `/admin`.
- **A host manages their own hosted ROMs.** Every `/host/<public_id>` route answers 404
  to a host who did not create that hosted ROM, unless they are an admin. `/host` lists
  the signed-in host's own.
- **Credentials.** A hosted download with both slots claimed writes slot 0's claimant
  into position 0 and slot 1's into position 1, each with their own `key_slot0`. With
  one slot claimed, the claimant goes in both positions with both their keys, which is
  exactly the ROM they would download themselves. A download needs at least one claim.
- **Entries are made at download, not at claim.** "Entered in a seed" keeps meaning a ROM
  was made for the player. The download upserts each claimant's entry with the hosted
  options, so a claimant's entry name and bag are the hosted ROM's, as they would be after
  any download; an entry with a recorded round is left as it is, as always.
- **A claim takes the lowest free slot.** Claiming is a POST from the claim page, one
  `UPDATE` that fills the lowest free slot; the GET only shows it. A user who already
  holds a slot of the hosted ROM claiming again is a no-op, and a full hosted ROM
  refuses. Players who care which controller they get scan one at a time and the host
  reloads between them; whose round it is does not depend on it.
- **One token per hosted ROM.** The hosting page shows one QR code, unchanged while it
  shows, so both players can scan at once. The token never changes; a link that has gone
  further than intended is answered by a new hosted ROM.
- **Releasing frees one slot.** The host can release either slot, which clears its user;
  the next claim fills it. A mistaken scan is undone without a new hosted ROM.
- **The QR code hides after a download.** A download sets `downloaded_at`, and a release
  clears it. The hosting page shows the QR code only while a slot is free and
  `downloaded_at` is NULL, so a ROM already on a cartridge does not quietly fall behind a
  later claim; releasing a slot, even a free one, shows the code again. The claim link
  itself keeps working, and each download builds from the slots as they are then.
- **Hosting never changes anyone's saved settings.** Unlike `seed_patch`, the hosted
  download saves nothing to the host's or a claimant's account and sets no download
  cookie, and the settings form neither reads nor writes saved settings. The hosted
  options are stored on the hosted ROM, starting from the defaults; the host sets the
  name and bag. A claimant's entry, which is not saved settings, still records the hosted
  options as any download's does.
- **Hosted file names tell pairs apart.** A hosted ROM downloads as
  `<NAME>_<public_id>_<download_stem>.nes`, e.g.
  `ALICE_7fK2pQx9Lm_notgr_par72_4np03ChsZL.nes`: the player 1 name of the hosted options
  (A-Z, `.` and space, spaces as `_`), the hosted ROM's public ID, then the seed's usual
  name. The parts that differ between pairs come first, so they survive an Everdrive
  N8's file list cutting long names short; the N8's bottom bar shows the whole name of the
  selected file. `hosted_download_stem` sits beside `download_stem` in `server/views.py`,
  and the hosting page passes it to `download.js` as `data-filename` and shows it beside
  the claimants.
- **Host this seed again.** The hosting page has a button that creates the next hosted
  ROM of the same seed, posting to the create route, so the next pair needs no pasted
  link.
- **Tokens** are 128-bit `secrets.token_urlsafe` values, unique, valid until the hosted ROM
  expires: `HOSTED_ROM_HOURS` (a constant, 12) after creation. An expired hosted ROM
  refuses claims and downloads; the host makes a new one.
- **Withdrawn seeds** refuse creation, claims and downloads, as the public download does.
- **Server-rendered QR codes.** The hosting page draws the claim link as an inline SVG
  from the repo's own encoder (`golf/qr/encoder.py`: version 5, level M, byte mode, up to
  84 bytes, ample for `<base_url>/claim/<token>`), with a small matrix-to-SVG function
  beside `golf/qr/render.py`. The `qrcode` package is a dev dependency only and the
  server is deployed without dev dependencies. A claim URL too long for version 5 fails
  at creation, not on the page. No script.
- **No polling.** The hosting page has a reload link; claims show on reload. Players
  claim before the host downloads, so nothing needs to arrive live.
- **Host pages are written in English, like admin pages.** Only hosts see them, so
  `server/templates/host/` holds its English directly and calls no `t()`, and the test
  that admin templates use no strings covers the host templates too. The one catalog
  text is `download.js`'s: the hosting page applies the IPS to the host's stored ROMs as
  the seed page does, and embeds `strings.for_script(DOWNLOAD_SCRIPT_STRINGS)` for its
  status messages, noted as the exception in `server/CLAUDE.md`.
- **Audit.** Creating, releasing and downloading are actions in `server/audit.py`, with
  a new `hosted_rom` target, recorded under the host's `users.id` in `admin_actions`
  (whose `admin_id` names whoever acted). A download's row names the claimants, so the
  audit log answers "who built a ROM for whom". Claims are players' actions and are not
  audited; they are on the hosting record.
- **Generalizes to player 2 invites.** Nothing in the tables or the claim route assumes
  the host is a configured host or that slot 0 is unclaimed. Opening this to players
  means a route open to every signed-in user that creates a hosted ROM with slot 0
  pre-claimed by its creator; the host check is in the host routes only.

## Club rules

Any club rule removes CHOOSE CLUBS from the ROM (`unfinished_steps`,
`choose_clubs=course.clubs == ClubRules()`). `InitializeSram` copies the one default bag
(`DefaultClubBagTable`, bank 9 `$AE23`) into both `Player1ClubBag` and `Player2ClubBag`,
and play reads each player's own bag by `CurrentPlayerIndex`; with CHOOSE CLUBS gone,
nothing else writes either bag. So:

- **No club rules**: both players start with the hosted bag and change it in game.
- **Required bag**: both players carry it, whatever max or bans come with it.
- **A max or banned clubs without a required bag**: both players are held to the one
  bag the host chooses.

The event's rule is that a hosted seed has either no club rules or a required bag. The
hosting page shows a prominent warning for any other seed (club rules that are not
`ClubRules()` and have no `required_bag`), from creation on and whatever the claims. It
warns and does not refuse.

Per-slot bags are a follow-up: a second default bag table that `InitializeSram` copies
to `Player2ClubBag` ($6035), each claimant choosing on the claim page. It changes what
finishing writes, so it is a finish ABI bump. The ADR records it as the revisit
condition, and the devplan's player 2 item names it.

## Storage

One migration, appended to `server/migrations.py`:

| Table | Columns |
|---|---|
| `hosted_roms` | `id`, `public_id` (base62, its `/host/` URL), `seed_id`, `host_id` (`users.id`), `token` (unique), the hosted options as `SavedSettings` JSON (NULL until saved), `created_at`, `expires_at`, `downloaded_at` (set by a download, cleared by a release) |
| `hosted_rom_slots` | `hosted_rom_id`, `slot` (0 or 1, `CHECK`), `user_id` (NULL until claimed), `claimed_at`. Primary key (`hosted_rom_id`, `slot`) |

`server/hosting.py` is the only code that writes them and the only place tokens are drawn:
`create_hosted_rom`, `load_hosted_rom`, `load_by_token`, `claim_slot`, `release_slot`,
`save_hosted_options`, `mark_downloaded`, and `hosted_roms_for_host` for `/host`. Tokens
never go in a log or a `repr`.

## Routes

| Route | Who | What |
|---|---|---|
| `GET /host` | host | A form taking a seed's link or ID, and the host's hosted ROMs, newest first: seed, claimants, expiry (expired ones marked) |
| `POST /host` | host | Create a hosted ROM for the seed the form names; redirect to it. An unknown or withdrawn seed goes back to `/host` saying so |
| `GET /host/<public_id>` | its host, or an admin | The hosting page: the club-rule warning when it applies, each slot's claimant and a release button, the claim QR code and link while a slot is free and no download has followed the last release, a reload link, the settings form, the file name, the download button, and Host this seed again |
| `POST /host/<public_id>/slots/<slot>/release` | its host, or an admin | Release a slot |
| `POST /host/<public_id>/settings` | its host, or an admin | Save the hosted options |
| `POST /host/<public_id>/patch.ips` | its host, or an admin | Upsert claimants' entries, finish with their credentials, record the download, return the IPS |
| `GET /claim/<token>` | anyone | Signed out: a sign-in link returning here. Signed in: the seed and a confirm button; or the slot they hold, or that the hosted ROM is full, expired or its seed withdrawn |
| `POST /claim/<token>` | signed in | Claim the lowest free slot; redirect back to the GET showing it |

`/host` and `/claim/` are new parts of the URL space: `server/routes/host_pages.py` with
`host_router(templates)` and `server/routes/claim_pages.py` with `claim_router(templates)`,
both included in `create_app`.

The download reuses `DownloadState.from_form`, `check_rom_hashes`,
`player_options_from_state` and `SeedBuilder.finish` as `seed_patch` does, with
credentials from `credentials_for_slots`, and skips `save_settings` and the download
cookie.

## Running it at the event

1. The host rolls or finds the seed, copies its link, opens `/host` from a bookmark,
   pastes the link and presses Host.
2. Each player scans the QR code, signs in with Discord if they are not already, and
   confirms. Players who care who is player 1 scan in that order, the host reloading
   between them.
3. The host reloads, checks both names against player 1 and player 2, sets player 1's
   name (and the bag, on a seed without club rules), and downloads the ROM. For one
   player, the host downloads after one claim.
4. The host copies the file, named for player 1 and the hosted ROM, to the pair's
   cartridge, then presses Host this seed again for the next pair.
5. The players play; each scans their own end-of-round QR code as usual.

A host who closes the tab finds the hosted ROM again in `/host`'s list.

## Development plan

Each item is about one pull request and ends with tests passing.

1. **Credentials per position, rounds by key.** In `golf/randomizer/build.py`,
   `credentials_for_slots(qr_seed_id, slot0, slot1)`, each a (player ID, key) pair;
   `credentials_for` becomes a call through it. In `server/submissions.py`, a position 1
   payload that fails under `key_slot1` is tried under `key_slot0` and recorded in round
   slot 0. Unit tests for `credentials_for_slots` with two players and one player in both
   positions; for `submit_scan`, a position 1 payload under `key_slot1` (round slot 1, as
   today), under `key_slot0` (round slot 0), a position 0 payload under `key_slot1`
   (refused, as today), and a position 1 own round arriving after a position 0 round on
   the same entry (answered with the first, which the first-submission rule keeps). An integration test finishing a
   ROM with two players and checking each position's credential bytes are in it. Update
   the module docstrings of `server/submissions.py`, `server/rounds.py` and
   `server/entries.py`: round slot is whose round it is, read from the column and never
   the payload.
2. **Storage.** The migration and `server/hosting.py`. Database tests: the first claim
   takes slot 0 and the second slot 1, the same user claiming again (no-op), a third user
   on a full hosted ROM (refused), releasing slot 0 with slot 1 held (the next claim takes
   slot 0), a download setting `downloaded_at` and a release clearing it, two hosted ROMs
   of one seed kept apart, `hosted_roms_for_host` listing only its host's, expiry, a
   withdrawn seed, the audit rows for creating, releasing and downloading, and that a
   token is absent from every `repr`.
3. **Host pages.** `Config.host_users` and `is_host`; `server/routes/host_pages.py` with
   `require_host`; `server/templates/host/index.html` and `hosted.html` with their
   English written directly; `hosted_download_stem`; the QR SVG function (with a test
   decoding its output through `golf/qr/decode.py` or `zxing-cpp`); the admin
   audit log's labels for the hosting actions and its link to a hosted ROM;
   and the download. Tests in `tests/unit/test_server_host.py`: `GOLF_HOST_USERS` parsed
   and its `dev:` rule enforced; a signed-out user and a signed-in non-host get 404 on
   every host route; a host who is not an admin gets 404 on `/admin`; an admin is a host;
   a host gets 404 on another host's hosted ROM, and an admin does not; creation from a
   seed link and from a bare ID, and refused for an unknown or withdrawn seed; Host this
   seed again; the download's credentials for two claims, one claim and none (refused);
   entries upserted for each claimant; no saved settings changed for the host or either
   claimant and no download cookie set; the settings form starting from the defaults; the
   file name, with a space in the name; the QR code hidden once both slots are claimed,
   hidden after a download with a slot free and shown again after a release; the
   club-rule warning (shown for a max or bans without a required bag; absent with no club
   rules and with a required bag); and the audit rows.
   `tests/integration/test_server_download_rom.py` gains a two-player hosted download.
4. **Claim page.** `server/routes/claim_pages.py`, `server/templates/claim.html` and
   `server/strings/claim.toml` with keys, notes and empty text. Tests in
   `tests/unit/server_app/test_claim.py`: signed out shows the sign-in link with the
   return path; confirm claims; a held slot, a full hosted ROM, an expired token, an
   unknown token and a withdrawn seed each show their state; the GET changes nothing.
   Capture `/claim/<token>`, `/host` and the hosting page with `golf-site-screenshot`
   (`--login` for the host pages), especially the claim page at phone width.
5. **Docs and ADR.** Draft an ADR (`golf-adr`, proposed, `--drafted-by Claude`): a round
   is credited by the key that signed it, so round slot 0 is the owner's own round in
   either position; a hosted ROM's slots are claimed by signed-in players through one
   link; hosting is a configured role beside admin; considered and rejected: claiming a
   round when its QR code is scanned, players signing in on the host's laptop, an
   `entries.slot1_owned` flag with refusal rules, and making hosts admins; revisit when
   opening to players (player 2 invites) or adding per-slot bags. Update:
   - `docs/randomizer_devplan.md`: the data model table (round slot's meaning), the
     routes table, the configuration lines (`GOLF_HOST_USERS`), the paragraph saying the
     player ID is written to both ROM slots, and the "Player 2's account" polish item
     (hosting as its model, per-slot bags as the follow-up).
   - `server/CLAUDE.md`: the routes list (`host_pages.py`, `claim_pages.py`), a Hosting
     section beside Admin (`require_host`, own hosted ROMs only, English templates), the
     Database section (`server/hosting.py` owns the hosting tables and tokens; tokens stay
     out of logs), the strings file list (`claim.toml`), the `download.js` exception for
     the hosting page, and the test file list.
   - `deploy/golf-site.env.example`: `GOLF_HOST_USERS`.
   - `docs/README.md`: this plan.

## Not Claude's to write

- Every `claim.*` string's `text`.
