You are writing test searches for a family photo archive's search engine. You will NOT see any photos. You get a summary of the archive: its folder names with photo counts, the spread of capture years, how many photos are scans of film or prints, black-and-white versus colour, and the kinds of cameras.

Write category searches: queries the owner might type that have MANY right answers in an archive like this one. Examples: "black and white film photos of mountains", "beach holidays with the kids", "scanned prints from the 70s", "snow", "birthday parties", "photos of dogs", "night city streets", "screenshots".

Spread them across:
- `subject`: people, animals, objects ("dogs", "babies", "cakes", "cars");
- `scene`: settings and landscapes ("beaches", "mountains in winter", "city at night", "kitchens");
- `event`: occasions and activities ("weddings", "skiing", "hiking", "christmas", "concerts");
- `attribute`: properties of the photo itself ("black and white", "film scans", "old faded prints", "panoramas", "screenshots", "selfies");
- `place_time`: places and eras from the folder summary ("greece", "photos from 2016", "the 2000s").
- `combo`: two of the above together ("black and white beach photos", "snowy mountains in greece").

Rules: lowercase, no full stop, 1–7 words, no names of people. Prefer searches a real person would type. Avoid near-duplicates.

Output one JSON object per line: `{"n": <1-based number>, "facet": "<facet>", "query": "<the query>"}`
