# Exact and same-pixel groups over ImportantFiles (task 6.1)

Store `results/dedup-groups/dedup-groups-v1/829d8c8b9a9f1261`, from `frame-2026-10-12.parquet` and the `pixelhash-v1` store after the full derive (task 1.4). Counts only; folder = the first two path levels (a folder named after an email address is redacted).

| Type | Groups | Assets | Locations |
|---|---|---|---|
| exact | 16,354 | 16,354 | 35,212 |
| same_pixels | 288 | 579 | 838 |

## exact: groups by source/folder pair

A group spanning n folders counts once for each of its folder pairs.

| Folder A | Folder B | Groups |
|---|---|---|
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/photos | 11,320 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Phone Photos | 1,517 |
| importantfiles:Pictures/(email-named folder) | importantfiles:Pictures/Google Photos | 762 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/ready to upload | 528 |
| importantfiles:Pictures/Greece | importantfiles:Pictures/Jude Profile | 258 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Misc | 232 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Judes Photos | 224 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/WWW | 213 |
| importantfiles:Pictures/100NCD70 | importantfiles:Pictures/Google Photos | 199 |
| importantfiles:Pictures/Misc | importantfiles:Pictures/photos | 145 |
| importantfiles:Pictures/Phone Photos | importantfiles:Pictures/photos | 131 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Italy2007 | 112 |
| importantfiles:Pictures/Football 21June2014 | importantfiles:Pictures/Google Photos | 105 |
| importantfiles:Pictures/Family New Year | importantfiles:Pictures/photos | 95 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Kyra | 91 |
| importantfiles:Pictures/Kyra 2nd birthday | importantfiles:Pictures/photos | 77 |
| importantfiles:Pictures/photos | importantfiles:Pictures/street | 68 |
| importantfiles:Pictures/Export | importantfiles:Pictures/Google Photos | 64 |
| importantfiles:Pictures/Karen and Evan wedding 2006 | importantfiles:Pictures/photos | 61 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Kyra and Toren | 52 |
| importantfiles:Pictures/200711 | importantfiles:Pictures/Google Photos | 51 |
| importantfiles:Pictures/200711 | importantfiles:Pictures/photos | 51 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Lily's room Aug2013 | 51 |
| importantfiles:Pictures/200709 | importantfiles:Pictures/photos | 50 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Kyra 2nd birthday | 50 |
| (other pairs) | | 1,283 |

## same_pixels: groups by source/folder pair

A group spanning n folders counts once for each of its folder pairs.

| Folder A | Folder B | Groups |
|---|---|---|
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/photos | 119 |
| importantfiles:Pictures/Google Photos | (same folder) | 105 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Kyra | 60 |
| importantfiles:Pictures/Kyra | importantfiles:Pictures/photos | 60 |
| importantfiles:Mazda Documents/from-memory-stick | (same folder) | 15 |
| importantfiles:Pictures/photos | (same folder) | 11 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Phone Photos | 9 |
| importantfiles:Pictures/Export | (same folder) | 8 |
| importantfiles:Pictures/Ligtroom-Exports | importantfiles:Pictures/for-instagram | 7 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/ready to upload | 4 |
| importantfiles:Pictures/Saved Pictures | (same folder) | 3 |
| importantfiles:Pictures/photos | importantfiles:Pictures/ready to upload | 3 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Misc | 2 |
| importantfiles:Pictures/Processed | (same folder) | 1 |
| importantfiles:For Facebook | importantfiles:For Facebook/Untitled Export | 1 |
| importantfiles:Pictures/toren phone | (same folder) | 1 |
| importantfiles:Pictures/Google Photos | importantfiles:Root files | 1 |
| importantfiles:Pictures/Italy2007 | importantfiles:Pictures/Misc | 1 |
| importantfiles:Pictures/Google Photos | importantfiles:Pictures/Saved Pictures | 1 |
| importantfiles:Pictures/Saved Pictures | importantfiles:Pictures/photos | 1 |
| importantfiles:Mazda Documents/from-memory-stick | importantfiles:Pictures/Google Photos | 1 |
| importantfiles:Pictures/2016 | (same folder) | 1 |
| importantfiles:Pictures/Kyra | (same folder) | 1 |
