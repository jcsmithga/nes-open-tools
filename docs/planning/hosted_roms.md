# Hosted ROMs: Development Plan

> **Note**: This document was written by Claude. It is the plan for letting an admin
> build a seed's ROM for other players, who claim its player slots from their phones, so
> their rounds are credited to their own accounts.

## Goal

At an in-person event, players with a NES and an Everdrive or a MiSTer often bring no
laptop, and one person rolls and copies seeds for everyone. Today that person can only
give them a ROM carrying the host's own credentials or a guest ROM, and either way the
players' rounds are not recorded as theirs. A hosted ROM fixes that: the admin opens a
hosting page for a seed, each player scans a QR code on it with their phone and confirms
on the site, and the admin downloads a ROM whose player 1 and player 2 slots carry the
two claimants' credentials.

In scope:

- Admin-only hosting: creating a hosted ROM, claiming its slots, releasing a slot,
  downloading the ROM.
- The data model and credentials built so that the same claim can later serve the
  general player 2 question (`docs/randomizer_devplan.md`, "Player 2's account"): a
  downloader holding slot 0 and inviting a teammate into slot 1.

Out of scope:

- Opening hosting to players who are not admins.
- Per-slot bags (see "Club rules").
- Patching player 2's name. The game names player 2 itself.

## Terms

- **Slot**: one of a ROM's two players, 0 (player 1) and 1 (player 2). The payload's
  `player_slot` names it, and each slot carries its own player ID and MAC key
  (`QrCredentials`, `golf/core/patches/qr_credentials.py`).
- **Hosted ROM**: a seed's ROM an admin builds for the players who claim its slots. It is
  a record on the server until downloaded, and can be downloaded again.
- **Host**: the admin who created the hosted ROM.
- **Claim**: a signed-in player taking one slot of a hosted ROM by opening its claim link
  and confirming.
- **Claim link**: `/claim/<token>`, one per slot, shown on the hosting page as a QR code
  only while that slot is the next to claim.

## What already works

No change to the ROM, the QR protocol, scan submission or the `rounds` table is needed:

- An entry (`server/entries.py`) is one per (seed, user) and holds a MAC key per slot.
- `QrCredentials` holds a player ID and a key per slot. Only `credentials_for`
  (`golf/randomizer/build.py`) puts the same player in both.
- `submit_scan` (`server/submissions.py`) finds the entry by (seed ID, player ID) and the
  key by the payload's slot.

So a ROM carrying player A's ID and A's slot 0 key in slot 0, and player B's ID and B's
slot 1 key in slot 1, records A's round against A's entry in slot 0 and B's against B's
entry in slot 1, under the existing first-submission rule.

## Decisions

- **Credentials per slot.** A hosted download with both slots claimed writes each
  claimant into their slot with that slot's key from their own entry. With one slot
  claimed, the claimant goes in both slots with both their keys, which is exactly the ROM
  they would download themselves. A download needs at least one claim.
- **Entries are made at download, not at claim.** "Entered in a seed" keeps meaning a ROM
  was made for the player. The download upserts each claimant's entry with the hosted
  options, so a claimant's entry name and bag are the hosted ROM's, as they would be after
  any download.
- **A claim is one slot, by one signed-in user.** Claiming is a POST from the claim page;
  the GET only shows it. A claimed slot can be claimed again only by the same user (no-op).
- **Slots are claimed in order.** The hosting page shows only player 1's QR code until
  slot 0 is claimed, then shows that claimant and player 2's QR code, so nobody has to aim
  at the right one of two codes. The server enforces the order: slot 1's link refuses a
  claim while slot 0 is free, and refuses slot 0's claimant, since the likeliest mistake is
  player 1 scanning again. A hosted ROM for one player is slot 0 claimed and slot 1 left
  alone.
- **Releasing starts the slot over.** The host can release a slot, which clears its user
  and draws a new token, so a mistaken scan is undone without a new hosted ROM. Releasing
  slot 0 releases slot 1 too, keeping the order: the page goes back to player 1's code.
- **Claims stay open after a download.** Each download builds from the slots as they are
  then. The hosting page shows when it was last downloaded and whether claims changed
  since.
- **Tokens** are 128-bit `secrets.token_urlsafe` values, unique, valid until the hosted ROM
  expires: `HOSTED_ROM_HOURS` (a constant, 12) after creation. An expired hosted ROM
  refuses claims and downloads; the host makes a new one.
- **Withdrawn seeds** refuse creation, claims and downloads, as the public download does.
- **Server-rendered QR codes.** The hosting page draws each claim link as an inline SVG
  from the repo's own encoder (`golf/qr/encoder.py`: version 5, level M, byte mode, up to
  84 bytes, ample for `<base_url>/claim/<token>`), with a small matrix-to-SVG function
  beside `golf/qr/render.py`. The `qrcode` package is a dev dependency only and the
  server is deployed without dev dependencies. A claim URL too long for version 5 fails
  at creation, not on the page. No script.
- **No polling.** The hosting page has a reload link; claims show on reload. Players
  claim before the host downloads, so nothing needs to arrive live.
- **Audit.** Creating, releasing and downloading are admin actions in `server/audit.py`,
  with a new `hosted_rom` target. A download's row names the claimants, so the audit log
  answers "who built a ROM for whom". Claims are players' actions and are not audited;
  they are on the hosting record.
- **Generalizes to player 2 invites.** Nothing in the tables or the claim route assumes
  the host is an admin or that slot 0 is unclaimed. Opening this to players means a
  non-admin route that creates a hosted ROM with slot 0 pre-claimed by its creator; the
  admin check is in the admin routes only.

## Club rules

Any club rule removes CHOOSE CLUBS from the ROM (`unfinished_steps`,
`choose_clubs=course.clubs == ClubRules()`), and `sram_defaults` copies one default bag
into both players' bags. So:

- **No club rules**: both players start with the hosted bag and change it in game.
- **Required bag**: both players carry it anyway.
- **A max or banned clubs without a required bag**: both players are held to the one
  bag the host chooses. When both slots are claimed on such a seed, the hosting page says
  so beside the bag. For the event, avoid two-player hosting on those seeds.

Per-slot bags are a follow-up: a second default bag table that `InitializeSram` copies
to `Player2ClubBag` ($6035), each claimant choosing on the claim page. It changes what
finishing writes, so it is a finish ABI bump. The ADR records it as the revisit
condition, and the devplan's player 2 item names it.

## Storage

One migration, appended to `server/migrations.py`:

| Table | Columns |
|---|---|
| `hosted_roms` | `id`, `public_id` (base62, its admin URL), `seed_id`, `host_id` (`users.id`), the hosted options as `SavedSettings` JSON, `created_at`, `expires_at`, `downloaded_at` (NULL until downloaded) |
| `hosted_rom_slots` | `hosted_rom_id`, `slot` (0 or 1, `CHECK`), `token` (unique), `user_id` (NULL until claimed), `claimed_at`, `claims_changed_at`. Primary key (`hosted_rom_id`, `slot`) |

`server/hosting.py` is the only code that writes them and the only place tokens are drawn:
`create_hosted_rom`, `load_hosted_rom`, `load_by_token`, `claim_slot` (one `UPDATE ...
WHERE user_id IS NULL OR user_id = ?`), `release_slot`, `mark_downloaded`, and
`hosted_roms_for_seed` for the admin seed page. Tokens never go in a log.

## Routes

| Route | Who | What |
|---|---|---|
| `POST /admin/seeds/<id>/host` | admin | Create a hosted ROM; redirect to it |
| `GET /admin/hosted/<public_id>` | admin | The hosting page: each claimed slot's claimant and warnings, the next slot's QR code and link, a reload link, the settings form, the download button |
| `POST /admin/hosted/<public_id>/slots/<slot>/release` | admin | Release a slot (slot 0 releases both) and redraw its token |
| `POST /admin/hosted/<public_id>/settings` | admin | Save the hosted options |
| `POST /admin/hosted/<public_id>/patch.ips` | admin | Upsert claimants' entries, finish with their credentials, return the IPS |
| `GET /claim/<token>` | anyone | Signed out: a sign-in link returning here. Signed in: the seed, the slot, and a confirm button; or that the slot is taken, or expired |
| `POST /claim/<token>` | signed in | Claim the slot; redirect back to the GET showing it claimed |

The admin routes go in `server/routes/admin_pages.py`. `/claim/` is a new part of the URL
space: `server/routes/claim_pages.py` with `claim_router(templates)`, included in
`create_app`.

The download reuses `DownloadState.from_form`, `check_rom_hashes`,
`player_options_from_state` and `SeedBuilder.finish` exactly as `seed_patch` does, with
credentials from `credentials_for_slots`. The admin's browser applies the IPS to its
stored ROMs with `download.js`, as on the seed page (see "Open questions").

## Running it at the event

1. The admin opens the seed's admin page and presses Host.
2. Player 1 scans the QR code, signs in with Discord if they are not already, and
   confirms.
3. The admin reloads. The page shows player 1's name and player 2's QR code.
4. Player 2 scans and confirms; the admin reloads, checks both names, and downloads the
   ROM. For one player, the admin downloads after step 3.
5. The players play; each scans their own end-of-round QR code as usual.

## Development plan

Each item is about one pull request and ends with tests passing.

1. **Credentials per slot.** In `golf/randomizer/build.py`, `credentials_for_slots(
   qr_seed_id, slot0, slot1)`, each a (player ID, key) pair; `credentials_for` becomes a
   call through it. Unit tests for two players and for one player in both slots. An
   integration test finishing a ROM with two players and checking that each slot's
   credential bytes are in it; extend `tests/integration/test_server_submission_rom.py`
   to a slot 1 scan if the simulator can produce one, otherwise build the slot 1 scan
   with `scan_path` in a unit test against two entries.
2. **Storage.** The migration and `server/hosting.py`. Database tests: claiming a free
   slot, claiming a taken slot (refused), the same user claiming again (no-op), slot 1
   refused while slot 0 is free and refused to slot 0's claimant, releasing (new token, old
   one dead; slot 0 releasing both), expiry, and that a token is
   absent from every `repr`.
3. **Admin hosting.** The admin routes, `server/templates/admin/hosted.html` with its
   English written directly, the Host button and a list of the seed's hosted ROMs on
   `admin/seed.html`, the QR SVG function (with a test decoding its output through
   `golf/qr/decode.py` or `zxing-cpp`), the audit actions, and the download. Tests in
   `tests/unit/test_server_admin.py`: non-admins get 404, creation refused on a withdrawn
   seed, the download's credentials for two claims, one claim and none (refused), entries
   upserted for each claimant, the page showing only the next slot's QR code, the
   club-rule notice, and the audit rows. `tests/integration/test_server_download_rom.py` gains a two-player hosted
   download.
4. **Claim page.** `server/routes/claim_pages.py`, `server/templates/claim.html` and
   `server/strings/claim.toml` with keys, notes and empty text. Tests in
   `tests/unit/server_app/test_claim.py`: signed out shows the sign-in link with the
   return path; confirm claims; a taken slot, slot 1 before slot 0, slot 1 by slot 0's
   claimant, an expired token, a released token and a withdrawn seed each show their state; the GET changes nothing. Capture `/claim/<token>`
   and the hosting page with `golf-site-screenshot` (`--login` for the admin page),
   especially at phone width, where the claim page is used.
5. **Docs and ADR.** Draft an ADR (`golf-adr`, proposed, `--drafted-by Claude`): a ROM's
   slots are claimed one at a time by signed-in players, credentials are per slot, admin
   only for now; considered and rejected: claiming a round when its QR code is scanned,
   and players signing in on the host's laptop; revisit when opening to players (player 2
   invites) or adding per-slot bags. Update:
   - `docs/randomizer_devplan.md`: the data model table, the routes table, the paragraph
     saying the player ID is written to both ROM slots, and the "Player 2's account"
     polish item (hosting as its model, per-slot bags as the follow-up).
   - `server/CLAUDE.md`: the routes list (`claim_pages.py`), the Database section
     (`server/hosting.py` owns the hosting tables and tokens; tokens stay out of logs),
     the strings file list (`claim.toml`), and the test file list.
   - The module docstrings in `server/submissions.py` and `server/entries.py`, which
     say both slots carry the downloader's ID and that only a signed-in download creates
     an entry.
   - `docs/README.md`: this plan.

## Not Claude's to write

- Every `claim.*` string's `text`.

## Open questions

- **`download.js` on an admin page.** The hosting page needs the browser to apply the IPS
  to the stored ROMs, which is what `download.js` does, but the script's status
  messages are catalog strings, and admin pages hold no catalog text. Recommended: let
  the hosting page embed `strings.for_script(DOWNLOAD_SCRIPT_STRINGS)` for the script,
  noted in `server/CLAUDE.md` as the one exception (`test_admin_templates_use_no_strings`
  checks `t()` calls, which this adds none of). The alternative is downloading the IPS
  and applying it by hand, which is slower at the table.
- **Which rounds count as a player's own.** An entry records one round per slot. On a
  ROM a player downloads, both slots carry their ID, and a slot 1 round is taken to be a
  teammate's: it is recorded on the downloader's entry but would never count toward the
  downloader's stats, which would count slot 0 rounds only. A hosted slot 1 claimant's
  round is their own, yet it lands in slot 1 of their entry, and a scan cannot tell it
  from a teammate's, since both use the entry's slot 1 key. Without a rule, a player can
  also record two rounds of their own on one seed: slot 0 from their own ROM and slot 1
  from a hosted one, in either order. Proposed: an `entries.slot1_owned` column in this
  plan's migration, set when the player claims slot 1 of a hosted ROM. Stats count the
  slot 0 round, or the slot 1 round when `slot1_owned` is set. `claim_slot` refuses a
  slot 1 claim from a player with a slot 0 round on the seed, and `submit_scan` refuses a
  slot 0 scan for an entry with `slot1_owned` and a slot 1 round. Ordinary ROMs are
  unchanged. Undecided.
