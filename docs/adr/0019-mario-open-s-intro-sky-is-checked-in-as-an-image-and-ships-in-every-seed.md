+++
status = "accepted"
date = 2026-10-07
area = "randomizer"
permanent = true
revisit_when = "an original sky is drawn to replace it, or the Mario Open ROM stops being something the site may assume players can lawfully obtain"
drafted_by = "Claude"
supersedes = []
+++

# Mario Open's intro sky is checked in as an image and ships in every seed

## Context

The course intro scene draws the course's name (`JAPAN COURSE`, `U.S. COURSE`,
`U.K. COURSE`) across its sky. A randomizer seed plays one mixed course in every slot,
so whichever name shows is wrong. Mario Open Golf shows the same landscape with no name
and more cloud where the letters are, and `course_intro_sky` draws the top eight tile
rows of that sky in their place (`docs/course_intro_scene.md`, **The sky patch**).

Two rules of the project bear on using it in the randomizer:

- Nothing dumped from a ROM is committed; `golf-rehydrate` produces it locally.
- A seed's patch carries Mario Open data only when `required_roms` has had the player
  show that ROM, which today means a Mario Open hole or music track.

The patch first read the sky from the Mario Open ROM each time it was built. In the
randomizer that would have had the server read a second ROM on every build, or at
startup, and fail builds when it was missing.

## Decision

- Mario Open's sky is checked in as `golf/core/patches/data/course_intro_sky.png`, a
  256x64 image in the three colors of background palette 0. `golf-intro-sky` writes it
  from the Mario Open ROM, and a test compares the two when that ROM is present.
- `course_intro_sky` takes its sky from an image (`read_sky_image`), that one by
  default. The patch never reads the Mario Open ROM.
- The randomizer puts the patch in every seed, from unfinished build version 8, which
  also brought the Peach dress and signpost colors.
- `required_roms` does not count the sky. A seed with no Mario Open hole or music asks
  for the US ROM only, and its patch still carries the sky: 71 tiles of pattern data
  and their arrangement, about 1.3 KB compressed.

jdharms decided on 2026-10-07 that shipping this artwork to every player is acceptable,
at least for now.

## Rejected alternatives

- **Requiring the Mario Open ROM for every seed.** It keeps the rule whole, and puts a
  second ROM in front of every player for a cosmetic change.
- **The sky only on seeds that already require Mario Open.** The other seeds would keep
  a wrong course name, which is what the patch is for.
- **A sky drawn from scratch.** A friend of jdharms drew one without seeing Mario Open
  (`scene_uk-export.png`, 2026-10-07); it fits the patch and runs. jdharms chose Mario
  Open's sky for now. `read_sky_image` exists because of it, and takes any such image.
- **Reading the sky from the server's Mario Open ROM**, cached at startup, or dumped by
  `golf-rehydrate`. Either keeps the image out of the repository, and adds a second ROM
  the builder cannot work without. The music dumps in `data/music/` are already Mario
  Open data the build reads from the repository.

## Consequences

- The builder still reads one ROM. Built once per process, as the signpost step is,
  the sky costs a build about 2 ms.
- "A patch holds nothing from a ROM the player has not shown" has one exception, the
  sky, and the repository holds one image taken from a ROM now. Another such exception should
  get its own record and not lean on this one.
- `docs/randomizer.md`, which jdharms wrote alone, says a ROM check gates the vanilla
  data a patch sends. It does not describe the sky.
- Released seeds keep the sky they were built with. Replacing it is an image and a new
  build version; nothing else in the patch depends on whose sky it is.
- The mode-text row of the tournament and bet modes does not meet this sky. The
  randomizer's `menu_trim` leaves only stroke play, which never draws that row.

## Sources

- `golf/core/patches/course_intro_sky.py`, `tools/art/intro_sky.py`
- `docs/course_intro_scene.md`, **The sky patch**
- Session of 2026-10-07 (`session_01BoPckbciZcBtJ8iKugRHZf`): the timing of the ROM
  read, the question of the ROM check, the clean-room sky, the decision, and holding the
  patch out of the stack for now
