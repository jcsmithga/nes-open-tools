+++
status = "accepted"
date = 2026-10-19
area = "site"
permanent = true
revisit_when = "hosting opens to every player (player 2 invites), per-slot bags are added, or stats need to tell a player's own round on a hosted ROM from one on their own ROM"
drafted_by = "Claude"
supersedes = []
+++

# A round is credited by the key that signed it, and hosts build ROMs that signed-in players claim

## Context

At in-person events, players with a NES and an Everdrive or a MiSTer often bring no
laptop, and one person rolls and copies seeds for everyone. A ROM carries a player ID and
a MAC key per position (player 1 and player 2), and until now every ROM carried one
player: a signed-in download put the downloader's ID in both positions, their entry's
`key_slot0` in position 0 and `key_slot1` in position 1. So the person copying ROMs could
only hand out ROMs carrying their own credentials, or guest ROMs, and neither recorded
the players' rounds as theirs.

`submit_scan` chose the key by the payload's position and recorded the round in that
slot of the entry, so a round's slot was its position: slot 0 the downloader's own round,
slot 1 a teammate's on their ROM. A ROM built for two players would put the second
player's own round in their entry's teammate slot, where nothing could tell it from a
teammate's round on their own ROM, and would let a player record two own rounds on one
seed, one in each slot.

## Decision

**A round's slot is the key that verified it.** `submit_scan` verifies a position 0
payload under `key_slot0` only. A position 1 payload is tried under `key_slot1` first,
recorded in round slot 1, then under `key_slot0`, recorded in round slot 0. Round slot 0
is always the entry owner's own round, whatever controller it was played from, and a
second own round on a seed meets the first-submission rule like any repeat. The
payload's position byte is only the controller; code reads whose round it is from
`rounds.slot`, never from the stored payload.

**Hosts build ROMs that players claim.** A host is a user `GOLF_HOST_USERS` lists, or an
admin. `/host` creates a hosted ROM of a seed, one per pair, with a single claim link
shown as a QR code. A signed-in player opens `/claim/<token>` on their phone and confirms,
taking the lowest free slot: player 1, then player 2. The host can release a slot, and
downloads a ROM that writes each claimant with their own `key_slot0`, in the position
they hold; a lone claimant gets exactly the ROM they would download themselves. The
download creates each claimant's entry as any download does, and changes nobody's saved
settings. A host manages only their own hosted ROMs; an admin, any. Creating, releasing
and downloading are in the audit log.

The details, including the club rules a hosted seed should have and the hosted file
name, are in `docs/planning/hosted_roms.md`.

## Rejected alternatives

- **An `entries.slot1_owned` flag.** Set when a player claims player 2 of a hosted ROM,
  with stats counting slot 1 for such entries and two refusal rules to stop a second own
  round. It needed a column and rules in two places, where letting the key say whose
  round it is needs neither.
- **Claiming a round when its QR code is scanned.** The ROM would carry the host's
  credentials and the scanning player would take the round. Anyone holding the phone that
  scans could take it, and a round would belong to whoever scanned first.
- **Players signing in on the host's laptop.** Each player would sign in to Discord on
  someone else's machine, and sign out after; slow at a table, and a risk to their
  account.
- **Making the hosts admins.** An admin can void rounds and withdraw seeds. A separate
  role gives a helper at an event only what hosting needs.
- **One claim link per slot, claimed in order.** It needed rules for scanning the wrong
  code and for player 1 scanning twice. One link filling the lowest free slot lets both
  players scan at once.

## Consequences

- **Hosted ROMs depend on the key rule for good.** A hosted ROM carries a claimant's
  `key_slot0` in position 1, so the server must keep accepting a position 1 payload under
  `key_slot0` for as long as such ROMs are played. That is why this record is permanent.
- **Forgery odds double for position 1 payloads**, from 2⁻³² to 2⁻³¹ per scan against a
  32-bit MAC, behind the scan rate limit.
- **One bag for both players.** A ROM has one default bag, which `InitializeSram` copies to
  both players. A seed with no club rules lets players change it in game, and a locked bag
  needs no choice; a max or bans without a locked bag hold both players to one bag, and a
  bag over a max is refused at download. Per-slot bags need a second default bag table
  and a finish ABI bump.
- **The groundwork for player 2 invites.** Nothing in the hosting tables or the claim
  route assumes the host is configured or that slot 0 is free. Opening this to players is
  a route open to every signed-in user that creates a hosted ROM with slot 0 pre-claimed
  by its creator.
- **Player 2's name is not the claimant's.** The ROM holds one name, player 1's; the game
  names player 2 itself.

## Sources

- `docs/planning/hosted_roms.md`, the plan this was designed in.
- Design and implementation session with jdharms, 2026-10-19.
- Code: `server/submissions.py` (`_round_slot`), `server/hosting.py`,
  `server/routes/host_pages.py`, `server/routes/claim_pages.py`,
  `golf/randomizer/build.py` (`credentials_for_slots`).
