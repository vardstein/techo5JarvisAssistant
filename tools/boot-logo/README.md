# Boot logos

What the bootloader paints before Linux starts lives in kaeru, amonet's bootloader in `expdb`, as a
315×170 picture in a 6105-byte slot (see [Boot logo](../../docs/hardware.md#boot-logo-lk)). Each `.bin`
here is that slot with another picture in it.

- `cronos-wordmark.bin`: the TECHO5 mark. `install-show.py` writes it on a Show 5 2nd gen or a Show 8.
- `openjade-wordmark.bin`: the OpenJade lockup, for Jarvis Shows (Crown, Checkers and Cronos). The install
  leaves expdb alone; it goes in afterwards, on its own, with
  `tools/jarvis-show.py boot-logo --name "<the Show's name>"` (and back out with `--amazon-logo`).
- `openjade-wordmark-crown.bin`: the same lockup at 477×255, for the Show 8 (Crown), whose 8" panel made
  the 315×170 one look tiny. The same command writes this one on a Crown, together with the four
  numbers in kaeru that say how big the picture is (below).

## How the OpenJade one was made

`openjade/` holds the sources: the white jade mark and the Fira Code licence, both from the website
(`public/logos/openjade-white-jade.svg`, `public/fonts/`), and `lockup.svg`, which
`make-lockup.py` builds from them the way the website's hero lays them out, with the text outlined:

    openjade/make-lockup.py FiraCode-VF.woff2 openjade/openjade-white-jade.svg openjade/lockup.svg
    resvg openjade/lockup.svg lockup.png
    tools/linux/patch-lk-logo.py checkers-kaeru.bin kaeru-openjade.bin lockup.png \
        --bundle 335308 --in-place --colors 64

and the slot is bytes 335308 to 341413 of the result. 64 flat colours keep the mark's shading and
compress to 5124 bytes. `make-lockup.py` needs fontTools with brotli; `checkers-kaeru.bin` is the one in
amonet-checkers v2.0.1. The slot is the same in Crown's and Cronos' kaeru, so one file serves all three.

## The bigger one for Crown

kaeru draws the wordmark in one function (`0x4bd0f5f8` in Crown's kaeru), and the box it draws in is
written into four instructions there as immediates: `mvn r3,#314`, `subs r0,#170`, `addw r2,r4,#315` and
`add.w r5,r1,#170`, at file offsets 63490, 63500, 63512 and 63518. The 477×255 logo changes those to
476, 255, 477 and 255 and nothing else in the code; `boot_logo.py` holds the 32 bytes around them both
ways. That is about one and a half times as wide and as tall. kaeru unpacks the picture into the third page of its video memory,
which at 1280×800 is 2 MB, and the bigger picture is 486540 bytes, so it fits with room to spare.

The picture still has to go into the same 6105-byte slot, so it has fewer colours:

    openjade/make-lockup.py FiraCode-VF.woff2 openjade/openjade-white-jade.svg big.svg \
        --size 477x255 --width 448
    resvg big.svg big.png
    tools/linux/patch-lk-logo.py crown-kaeru-477x255.bin out.bin big.png \
        --bundle 288716 --size 477x255 --in-place --colors 20

`crown-kaeru-477x255.bin` is amonet-crown v2.0.1's kaeru with the four numbers already changed and any
477×255 bundle in the slot, since `patch-lk-logo.py` checks the old bundle unpacks to the new size. The
slot is bytes 288716 to 294821 of `out.bin`. 20 colours compress to 5875 bytes; 24 do not fit.

When the tool writes it, it writes the four numbers first and the picture second, and the other way
round when it takes it back out. Stopped in between, kaeru then has the bigger box with the smaller
picture, sees the size is wrong and skips the logo; the start goes on without one.

## The other themes

The website has four jade themes, and `openjade/` has each one's mark (`openjade-<theme>-jade.svg`).
`openjade/themes/` holds the lockup in every one of them, 315×170 like the boot slot:

- `lockup-<theme>-dark.svg`: on black, "Jade" in the theme's colour lifted towards white, as the
  boot logo has it. `lockup-white-dark.svg` is `lockup.svg`.
- `lockup-<theme>-light.svg`: on the theme's page colour, in the hero's colours.
- `lockup-<theme>-transparent.svg`: the hero's colours with no ground, to put on something light.

The themes are `white`, `sakura`, `leafgreen` and `ember`. One of them, made again:

    openjade/make-lockup.py FiraCode-VF.woff2 openjade/openjade-sakura-jade.svg out.svg \
        --theme sakura --ground light [--transparent]

`--size` and `--width` give another canvas and width, if a different size is wanted.
