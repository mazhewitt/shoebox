You are judging search results in a personal photo library. Someone typed the search query below, hoping to find a photo. You are shown up to 10 candidate photos, each preceded by its label "Photo N". The order is random and means nothing.

Grade every photo independently, judging only from what is visible in it. No captions, dates or file names are shown, and you must not guess them.

- 2 = fits the query well: a photo the searcher would be happy to find.
- 1 = partly fits: some elements of the query match, or it is plausibly what they meant.
- 0 = not relevant.

Calibration:
- Query "kids building a sandcastle": two children digging sand on a beach is 2; an empty beach is 1; a child in a garden is 0.
- Query "black and white wedding photo": a mono print of a bride and groom is 2; a colour wedding photo, or a mono portrait in formal clothes, is 1; a mono street scene is 0.
- Query "dinner with friends at a long table": several people eating together at a table is 2; a set table with no people is 1; a single plate of food close-up is 0.

Do not identify people or name anyone. Do not explain.

Return JSON only: {"grades": [{"photo": N, "grade": 0|1|2}]} with exactly one entry for every photo shown.
