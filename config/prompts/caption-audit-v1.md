# Caption audit (caption-audit-v1)

You are auditing machine-written captions for a personal photo library. The captions exist so the owner can find
photos later by typing a search. Each batch file (`batch-NNN.json`) lists photos; each photo has a `key`, an
`image` (a 1024 px JPEG thumbnail; open and view it) and up to five caption sets labelled `A` to `E`. The sets
come from different captioning systems in a random order per photo; the labels mean nothing and say nothing
about which system wrote them. A set whose `caption` is null is missing (the system produced nothing).

For every photo, in this order:

1. **View the photo** before reading any caption.
2. **Write the reference first**, from the image alone:
   - `description`: one neutral line saying what a searcher would want indexed (main subject, setting,
     activity, number of people, legible text, black-and-white/film/scan nature). Never name or identify anyone.
   - `medium` (what the picture originally was): one of `digital`, `film_negative`, `film_slide`, `print`,
     `document`, `screen`, `unknown`.
   - `digitiser` (how it became this file): one of `camera`, `phone`, `film_scanner`, `flatbed_scanner`,
     `camera_rephoto`, `screenshot`, `software`, `unknown`.
   - `colour_class`: one of `colour`, `mono`, `toned`, `unknown`.
   - `rotate_cw`: the clockwise rotation in degrees that would make the photo upright: `0`, `90`, `180` or
     `270` (0 when it is already upright).
   - `confidence`: for each of `medium`, `digitiser`, `colour_class`, `rotate_cw` one of `high`, `medium`,
     `low`. Say `high` only when the image settles it.
3. **Then audit each caption set** `A`..`E` (its caption, tags and structured fields together) and count:
   - `wrong`: false statements or tags, including a wrong `medium`, `digitiser`, `colour_class` or
     `rotate_cw` where your reference for that field is high-confidence. Count each false fact once.
   - `invented`: specific details the image does not support: guessed places, events, dates, ages,
     relationships, or text that is not in the picture.
   - `missing`: important searchable facts that are visible but absent from both the caption and the tags:
     the setting, the main subject, the activity, the number of people, legible text, and the
     black-and-white/film/scan nature of the photo.
   - `useful`: how useful the set would be for finding this photo by search, 1 (useless) to 5 (excellent).
   - `notes`: a few words on the main problems, or "" if none. Never name or identify people.
   For a set whose caption is null write `{"wrong": 0, "invented": 0, "missing": 0, "useful": 1, "notes": "no caption"}`.

Judge each set on its own against the image and your reference, not against the other sets.

**Output.** For batch `NNN`, write `audit-NNN.jsonl` in the same directory: one JSON object per line, one line
per photo in the batch, exactly this shape (every label shown for the photo present):

{"key": "c-001", "reference": {"description": "...", "medium": "print", "digitiser": "flatbed_scanner", "colour_class": "mono", "rotate_cw": 0, "confidence": {"medium": "high", "digitiser": "medium", "colour_class": "high", "rotate_cw": "high"}}, "sets": {"A": {"wrong": 0, "invented": 1, "missing": 2, "useful": 3, "notes": "..."}, "B": {...}, "C": {...}, "D": {...}, "E": {...}}}

The counts are non-negative integers; `useful` is an integer from 1 to 5. Write nothing else to the file.
