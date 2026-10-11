# Videos: catalogue only (metadata, dates, GPS, keyframes)

Status: ready-for-agent (after 02)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 5:** videos are catalogue-only for now, but rich:
  - full ExifTool and ffprobe metadata;
  - dates and GPS with provenance;
  - several keyframes stored, so content analysis can be added later without decoding again.
- **Scale:** ImportantFiles has 1,905 video files (1,332 unique).
- **Takeout is short-clip heavy:** Live Photo `.MP4`s and Android motion-photo clips. `dedup/` groups those with their still into one capture.

## What

- **Metadata:**
  - 02's ExifTool record covers QuickTime/Keys tags, including GPS (`©xyz`) and creation dates in UTC.
  - Add `ffprobe -v error -show_format -show_streams -of json` per unique video: duration, codec, resolution, rotation, frame rate, and audio present or not.
  - Store it in `results/metadata/ffprobe/<params_hash>/`, keyed by asset_id.
- **Dates and GPS:** these go through 05 and 06 like images. QuickTime `CreationDate` (with zone) wins over `CreateDate` (UTC).
- **Keyframes:**
  - five frames at 5/25/50/75/95% of the duration, or fewer for clips under 2 s;
  - extracted with ffmpeg (read-only input) and rendered through the `infer-v1` and UI profiles (09);
  - the 25% frame is the poster;
  - stored under `derived/keyframes/<asset_id>/<n>.jpg`.

  Keyframes are derived, but they cost a full read of the video. Decide whether `derived/keyframes` is excluded from backup like thumbnails, or kept.
- **Not in scope:** captioning video content. The bulk run is stills only. Record "video" as a capture kind so search can filter it.

## Done when

- Tests on fixture clips: phone MP4, iPhone MOV with rotation, a 1 s Live Photo clip.
- Every unique video in ImportantFiles has an ffprobe record, a date with its kind, and keyframes, or a listed error.

## Depends on

02 and 09. `dedup/` for still+clip pairing.

## Comments
