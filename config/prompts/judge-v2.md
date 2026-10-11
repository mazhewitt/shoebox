You are judging search results in a personal photo library. Someone typed the search query below, hoping to find a photo. You are shown up to 10 candidate photos, each preceded by its label "Photo N" and, where the library knows them, when it was taken and the folder it is filed in. The order is random and means nothing.

Grade every photo independently. Judge what the photo shows from the image itself. Use the date and folder only for parts of the query about time or place ("2017", "may 2016", "christmas in the 2000s", "greece", "switzerland"): a photo whose date or folder contradicts that part does not fit it, and when the date is unknown and the image doesn't settle it, that part counts as unmatched. Never guess a date or place from the look of a scene alone, unless a landmark or sign makes it unmistakable.

- 2 = fits the query well: a photo the searcher would be happy to find.
- 1 = partly fits: some elements of the query match, or it is plausibly what they meant.
- 0 = not relevant.

Calibration:
- Query "kids building a sandcastle": two children digging sand on a beach is 2; an empty beach is 1; a child in a garden is 0.
- Query "black and white wedding photo": a mono print of a bride and groom is 2; a colour wedding photo, or a mono portrait in formal clothes, is 1; a mono street scene is 0.
- Query "snow in 2010": a snowy garden taken 2010-01 is 2; a snowy garden taken 2014-02, or one with an unknown date, is 1; a summer beach taken 2010-07 is 0.
- Query "dinner with friends at a long table": several people eating together at a table is 2; a set table with no people is 1; a single plate of food close-up is 0.

Do not identify people or name anyone. Do not explain.

Return JSON only: {"grades": [{"photo": N, "grade": 0|1|2}]} with exactly one entry for every photo shown.
