# Yardage Books

> **Note**: This document was written by Claude, from a design worked out with jdharms.

A seed's yardage book is the rangefinder for that seed's course: its 18 holes in playing
order, with their transforms applied, each at the seed's pin and with the wind of its
tee shot. It is at `/h/<id>/book`, linked from the seed page, and stays up when the
seed is withdrawn, as the seed page does.

Code: `server/yardage_book.py` (the book's metadata), `server/seeds.py` (the stored holes),
`golf/rendering/hole_renders.py` (the renders), `server/templates/_rangefinder.html` and
`server/static/rangefinder/` (the viewer the book shares with `/rangefinder`).

## What a book shows

| | Source |
|---|---|
| Hole order and par | `seed_holes` |
| The hole | `hole_data`, as the seed's build made it |
| Yards | The hole's own `distance` |
| Pin | `seed_holes.pin_index`, the pin the hole's wind seed deals |
| Tee-shot wind | `tee_wind` in `server/yardage_book.py`: the first swing's direction and speed |

- **One pin.** The whole-hole image has the seed's pin drawn in, and the green view
  shows that pin's flag with no way to change it. A book is open while its round is
  played, and a pin that could be moved would have to be moved back.
- **The tee shot's wind only.** Under `seeded_wind` every player's first swing on a
  hole has the same wind (`docs/seeded_wind.md`). Each later swing's wind also follows
  from the manifest, and the book leaves it out: knowing the second shot's wind would
  decide the tee shot. Nothing hides it, since the manifest is public.
- **Direction** is the game's angle byte, clockwise from straight up the map, so the
  arrow points where the wind pushes the ball on the image below it.

## The holes

The seed's build hands its 18 holes to `insert_seed`, which stores each in `hole_data`
as zlib-compressed canonical JSON under its content hash, and names the row in
`seed_holes.data_hash` (ADR 0021, ADR 0024). A later release may transform a hole
differently, or correct its catalog entry, so the book reads only these rows: it needs
neither the catalog nor the hole store, and a hole the catalog has since withdrawn shows
as the seed's ROM holds it. A hole two seeds share, such as any untransformed vanilla
hole, is stored once, and a seed with every hole transformed adds about 20 KB.

## The renders

`/h/<id>/book.json` is the book's metadata, in the shape `metadata.json` has for the
rangefinder, with one course. Before answering it renders whatever its holes lack:

```
<GOLF_RANGEFINDER_DIR>/variants/<content hash>/main_pin_<K>.png
                                               green.png
                                               green_flag_<K>.png
```

- Every hole of a book goes through this cache, transformed or not, keyed by its
  content hash, so a hole two seeds share is rendered once. The rangefinder's own
  `images/` are not used.
- Only the seed's pin is rendered. Another seed with the same hole at another pin adds
  that pin's two images.
- A first view renders up to 18 holes, about half a second. Seed creation renders
  nothing.
- The renders are a cache. `golf-rehydrate` clears `variants/` whenever it renders the
  rangefinder, and nothing else removes them; a seed whose holes all came from a seeded
  transform takes about 400 KB on disk once viewed.
- `RENDER_VERSION` (`golf/rendering/rangefinder.py`) names what the renderer draws.
  Bump it when a change alters any image. `metadata.json` records it, so
  `golf-rehydrate --check` fails on older renders and the service's next start renders
  again, and a book's image URLs carry it as `?v=`, which the static mount serves as
  immutable.

## The page

`_rangefinder.html` holds the viewer for both pages. A book passes `book=true`, which
drops the flag indicator, adds the wind readout and marks the section
`data-kind="book"`, which hides the course selector and keeps the course out of the
permalink (`?hole=` only). The modules read the same metadata either way: a hole with
a `wind` shows it, and a hole with one flag image has no other to change to.
