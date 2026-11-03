# Footage & labels (you, ~60 min, before starting the run)

The footage you shoot now is both the test set Claude Code builds against **and** the footage in your demo video. Shoot it well once.

There is nothing to stage on the wall. At each defect you **point at it with your index finger, hold still for ~2 s, and say what's wrong**. SAM 3.1 follows your hand, and your fingertip tells Punchwalk where the defect is.

## Camera

**Ray-Ban Meta glasses (use them if you have them).** Hands-free and at eye level is how this would really be used, and Meta's judges will notice.
- Start and stop with the capture button (or "Hey Meta, record a video"). Clips max out at **3 minutes**, so keep each walk under 3 minutes or split it into parts at a room change.
- Import through the Meta AI app, save to your phone, then copy into `footage/`. Name parts in order: `walk1_a.mp4`, `walk1_b.mp4`.
- At each defect, **look straight at it** with your head still, then point (see below). For low defects, crouch or look down so the defect is in the middle of your view.
- Portrait video is fine.
- Also get 2–3 seconds of someone filming *you* wearing the glasses while you point at a defect (or prop a phone up). That's the opening shot of the demo video.

**Phone.** 1080p, 30 fps, **HDR off**, H.264 if your phone allows it (iPhone: Settings → Camera → Formats → Most Compatible; Record Video → 1080p at 30 fps; HDR Video off). Landscape, bright even light, smooth walking, no whip pans. Hold the phone in one hand and point with the other.

## How to point

1. Stop about 0.5–1 m away, look straight at the defect, and keep your head still.
2. Bring your hand into view and point with your **index finger** at the defect's edge, close to it but **not covering it**. Hold the finger still for **~2 seconds**.
3. While pointing (or right after), say one sentence the way a site super would: "Upper cabinet left of the sink, knob missing."
4. Drop your hand, pause about a second, move on.

One point per defect. Don't wave your hand around between defects, and don't point at things you don't describe.

## Stage 10 reversible defects across 3–4 rooms

| # | Room | Defect (how to stage it) | Trade |
|---|---|---|---|
| 1 | Kitchen | Cabinet knob missing (remove it) | carpentry_millwork |
| 2 | Kitchen | Coffee drips on counter | cleaning |
| 3 | Kitchen | Bulb out in a fixture (remove the bulb) | electrical |
| 4 | Bathroom | Towel bar loose (loosen a screw) | general |
| 5 | Bathroom | Mirror smudged / toothpaste splatter | cleaning |
| 6 | Bedroom | Door won't latch (push a wad of folded paper into the strike-plate hole) | doors_hardware |
| 7 | Bedroom | Scuff marks on wall (light pencil) | painting |
| 8 | Hallway | Switch plate crooked (loosen the screws only; don't remove it) | electrical |
| 9 | Living room | Window screen missing (remove it) | windows_glazing |
| 10 | Living room | Debris on floor (sawdust or crumbs) | cleaning |

Swap in whatever your place allows. Also add **one defect you describe without pointing** (keep your hands down). This tests the voice-only path.

## Walk 1: first walk (2–3 min) → `footage/walk1.*`

1. Say the room as you enter: "Kitchen."
2. At each defect: look, point and hold ~2 s, say the sentence (see "How to point").
3. Don't double back to a defect you've already described.

## Between walks

Fix 6 items. Leave 4 unfixed. Pick one of the unfixed items to **claim is fixed** on the re-walk.

## Walk 2: re-walk (1.5–2 min) → `footage/walk2.*`

Same route. At each original item, **point at the same spot again** (hold ~2 s) and say its status: "Kitchen cabinet knob, fixed." / "Bedroom scuffs, still there." For the item you picked, say "fixed" even though it visibly isn't.

## Walk 3: holdout (optional, 1–2 min) → `footage/walk3.*`

A different space with 3–4 new defects, pointed and described the same way. It's never used for tuning, only to show the system generalizes.

## Labels → `eval/labels/walk1.csv`, `walk2.csv`, `walk3.csv`

Copy `eval/labels_template.csv` and fill one row per item per walk. `time_mmss` = when you start describing the item.

- **`pointed`:** 1 if you pointed at it on that walk, 0 if you only described it (the voice-only item).
- **walk1 / walk3:** leave `fixed_in_rewalk` and `claimed_fixed_in_rewalk` blank.
- **walk2:** one row per original item you addressed, using the **same `item_no` as walk1**, with its re-walk time, `pointed` (did you point at it on the re-walk), `fixed_in_rewalk` (the truth: 1/0) and `claimed_fixed_in_rewalk` (what you said: 1/0).
- **Split walks:** if a walk is in parts, put the part letter (`a`, `b`) in `clip` and give `time_mmss` within that part. Leave `clip` blank for single-clip walks.

Trade values: electrical, plumbing, drywall, painting, carpentry_millwork, doors_hardware, flooring, windows_glazing, hvac, appliances, cleaning, general.

## Then

Go back to HUMAN_TODO.md, step 2.
