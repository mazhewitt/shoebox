# `private` visibility: hidden from family viewers in search and serving

Status: blocked (on `search/01`, `web-app/02`, `web-app/04`)
Reported: 2026-10-11
Reporter: mazda (request), filed by claude

## Why

- **Decision 6 says everyone sees everything,** and decision 4 lets the family search through Cloudflare Access.
- **Some photos must stay owner-only:** items the owner marks `private` in the screen review (03), and possibly later ones the owner marks by hand in the UI.

## What

- **Schema:** a `visibility` column per capture in the schema (`search/01`), `all | owner`. The default is `all`; it is set to `owner` by a `private` verdict, or by an owner correction in the UI (a human label, decision 14).
- **The API filters by caller** (`web-app/04` identity):
  - Family accounts never get `owner` captures, in search results, in counts or facets, or in thumbnail or original serving. A direct URL to one gets a 404, not a 403, so its existence isn't revealed.
  - `exclude` verdicts are not imported into the search index at all.
- **The owner's view** shows private items with a small marker, and can change visibility.

## Done when

- API tests: a family token gets no private capture by search, facet count, thumbnail URL or original URL. The owner token does.
- `web-app/07` (Gate D) includes these cases.

## Depends on

- `search/01`.
- `web-app/02`, `03` and `04`.

## Comments
