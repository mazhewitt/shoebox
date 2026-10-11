You are writing test searches for a family photo archive's search engine. The owner will one day type searches into a box to find a photo they remember. For each photo you are given, write ONE search query that the owner might plausibly type to find that photo again, in the style assigned to it.

You see the photo plus its capture date (may be missing or only a scan date), its folder path and its file name. The owner knows their own folders, trips and family, but remembers photos loosely: they rarely know exact details, and they type short queries.

## Styles

- `vague_recall`: a loose memory of the scene, 2–5 words, like "that snowy valley walk", "kids on the boat", "sunset over the sea". No metadata.
- `visual`: a plain description of what is in the picture, 3–8 words, like "girl in yellow jacket on slide", "red tram in a city street". No metadata.
- `attribute`: led by a property of the photo (black-and-white, film, scan, old print, night, close-up, portrait, panorama) plus the subject, like "black and white portrait of an old man", "faded print of a beach". No metadata.
- `place_time`: uses the place or time the owner would know from the folder path, file name or date, plus a subject word, like "greece 2018 ruins", "zermatt rope park", "christmas 2009". Only use place or time words that the metadata actually gives, or a landmark that is unmistakable in the photo.
- `people_role`: refers to people the way the owner would, by role or name taken ONLY from the folder path or file name (e.g. a folder "Dad's pics" → "dad"; "GRANDAD IN HELSINKI" → "grandad in helsinki"), plus what is happening. Never infer who someone is from their face or appearance.
- `text`: built around text legible in the photo (a sign, a shop name, a cake, a banner), like "happy birthday cake", "sign saying gap".

## Rules

- Write like someone typing into a search box: lowercase, no full stop, usually 2–8 words.
- The query should fit this photo well, but it doesn't have to be unique. Real searches are often vague; don't stuff in details nobody would remember (exact counts, colours of minor objects).
- Never copy the file name. Camera codes like "DSC_2530" or "L1003941" are never part of a query.
- No names of people unless they come from the folder path or file name, and only in the `people_role` style.
- If the assigned style cannot apply (`people_role` with no person words in the path, `text` with nothing legible, `place_time` with no usable place or date), write the query in the `visual` style instead and set `style_used` to `visual` with a short `fallback_reason`.

## Output

One JSON object per photo, on its own line, exactly:
`{"key": "<the photo's key>", "style_assigned": "<style>", "style_used": "<style>", "query": "<the query>", "fallback_reason": null or "<short>"}`
