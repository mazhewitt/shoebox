# Offline reverse geocoding: GPS to searchable place names

Status: ready-for-agent (after 02)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **PLAN.md Phase 2:** GPS → place names with an **offline** reverse geocoder (GeoNames), so places become searchable text.
- **Privacy:** an offline geocoder means no coordinates leave the machine.
- **Search:** the eval-1k queries include place searches. Today those reach the photo only through folder names (`Pictures/Greece`) or a caption's guess.
- **Decision 30:** Google place names from the sidecar are a second source, with provenance `google_photos`.

## What

- **Data:** the GeoNames `cities1000` dump plus `admin1CodesASCII` and `countryInfo`. Record the dump date and its hash. Keep it in `<shoebox_root>/reference/geonames/`, never in the repo, and record its licence (CC BY 4.0) in `docs/`.
- **Module:** `src/shoebox/catalogue/places.py`. A k-d tree (scipy) or a haversine ball tree over the cities.
  - Output per asset with GPS: `{lat, lon, source: exif|sidecar, city, admin1, country, distance_km}`, with provenance `geonames:<dump date>`.
  - Reject `0,0` and other sentinel coordinates.
  - Record the distance; beyond a cut-off (e.g. 25 km) the result is the region or country only.
- **GPS source:** EXIF GPS (02) first, then the sidecar's `geoDataExif` / `geoData` (03). Disagreements over 1 km are listed.
- **Searchable text:** city, region and country names, in English. Add an alias list later only if the search eval asks for it.
- **Hosted sends stay unchanged:** derived JPEGs carry no GPS. Place names are local metadata, used in search, not sent to Gemini.

## Done when

- Tests: known coordinates → expected city/country; sentinel rejection; distance cut-off.
- GPS coverage per source and per capture kind is reported. The 2026-10-10 frame's GPS share isn't summarised yet.
- **Precision:** Opus checks 100 GPS-bearing photos at city level, against the image plus folder name. Report precision; the bar is ≥ 0.98 at country level.

## Depends on

02.

## Comments
