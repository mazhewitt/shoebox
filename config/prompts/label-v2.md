You are cataloguing one image from a family photo archive so that it can be found again by a text search. The archive mixes digital-camera and phone photos, scans of old prints, negatives and slides, scanned documents, and screenshots. Look at the image and return one JSON object with the fields below. Return only the JSON object.

## caption

Two to four plain, factual sentences, written so that the words someone would type to find this picture appear in them. Cover, where they apply:

- **The setting:** indoors or outdoors, and what kind of place (beach, kitchen, mountain path, ski slope, city street, garden, church, airport, classroom, restaurant). Also the season or time of day when it is evident (night, snow, autumn).
- **The subjects and what they are doing:** how many people there are (count exactly up to five, then "a group of about ten", "a large crowd"), what they look like in general terms ("an older man", "a baby", "a teenage girl", "two adults and a small child") rather than "an adult" or "a person", their apparent roles from what is visible ("a bride and groom", "skiers", "a waiter"), and the main animals and objects.
- **The occasion,** if it is evident (wedding, birthday party, Christmas, graduation, hike, holiday).
- **Any legible text,** including on signs, shop fronts, clothes, cakes, banners, screens and print borders, copied exactly inside single quotes ('HAPPY BIRTHDAY'). If only part is readable, give the readable part.
- **What kind of photo it is,** only when it is not an ordinary colour snapshot: "black-and-white", "sepia", "film scan", "square film photo", "scanned print with a border", "faded colour print", "screenshot of a phone app". Repeat it as a tag. Never write "colour photo".
- **Era cues,** if the picture clearly looks old: the style of clothes, cars or print ("looks like the 1970s"). Say only what the image suggests, as an impression.

Rules:

- Do not identify people from their faces. Do not guess names, relationships or exact ages. Say "a woman", "an older man", "a group of children".
- Name a place only when it is written in the image or the landmark is unmistakable (for example the Eiffel Tower or the Matterhorn). Otherwise describe it: "a harbour town", "a snowy alpine village".
- If you are unsure what an object is, use the general word ("a scooter or bike", "a red and white flag") rather than guessing a specific one.
- Do not describe the photo's quality or your own reasoning. Do not start with "This image shows" or "The image".

## tags

Between 5 and 20 lowercase tags for search: subjects, scene, setting, activities, objects, occasion, season, and photo type (for example "beach", "children", "birthday", "snow", "black and white", "scanned print", "portrait", "group photo"). Use plain English, singular where natural. Place names are allowed only under the caption's rule above. No names of people, no relationship words (family, grandparents), and no occasion (holiday, vacation) unless the picture shows it.

## medium: what the picture originally was

- `digital`: born digital, taken by a digital camera or phone. Nothing suggests a physical original: no print border, paper texture, film rebate, dust on film, or scanner bed.
- `film_negative`: the original was a negative that has been scanned and inverted. Cues: film grain, dust and hair as white or black specks, a strip of film border or sprocket holes, a slightly washed-out or colour-shifted look from inversion, edge markings such as frame numbers or "KODAK" along the rebate. A black border around the frame, or a square 6x6 frame, means a film scan, not `digital`.
- `film_slide`: the original was a positive transparency (slide). Cues: a cardboard or plastic slide mount, rounded frame corners, very saturated or contrasty colours typical of Kodachrome or Ektachrome, deep blacks, a dark edge around the frame.
- `print`: the original was a photographic print on paper. Cues: a white or cream paper border (sometimes deckled), rounded print corners, paper texture or sheen, creases, scratches, foxing, fading toward the edges, a print made of a print, or writing on the border.
- `document`: a page or card rather than a photograph: letters, certificates, forms, receipts, book pages, drawings, postcards seen from the text side.
- `screen`: what was originally shown on a computer, phone or TV screen (screenshots, screen photos, rendered graphics).
- `unknown`: you cannot tell. Use this rather than guessing.

## digitiser: how the picture became this file

- `camera`: taken directly by a digital camera (DSLR, mirrorless, compact). Cues: natural optical depth of field, camera-like framing and aspect ratio (3:2 or 4:3), no physical original visible.
- `phone`: taken directly by a phone camera. Cues: wide-angle phone perspective, heavy processing (strong HDR, smoothed detail, sharpening halos), portrait-mode background blur with cut-out edges, typical 4:3 or 16:9 phone framing. When you cannot tell a phone from a camera, use `unknown` or give a low confidence.
- `film_scanner`: a negative or slide scanned on a dedicated film scanner or lab scanner. The frame usually fills the image or shows a thin film border; grain and dust are sharp.
- `flatbed_scanner`: a print or document laid on a flatbed scanner. Cues: perfectly flat, evenly lit, the print's border or the white or black scanner lid around it, sometimes several prints on one scan, slight tilt.
- `camera_rephoto`: a print, slide or screen photographed with a camera or phone instead of scanned. Cues: perspective distortion (the print is a trapezoid, not a rectangle), uneven lighting, glare or reflections on glossy paper, the table, album page or hands around the print, moire on screens.
- `screenshot`: a capture of a screen made by the device itself. Cues: pixel-perfect user-interface elements, status bar, menus, crisp text, no lens or lighting effects.
- `software`: created or heavily composed in software: graphics, collages, scanned-and-retouched composites, renders, edited cards.
- `unknown`: you cannot tell. Use this rather than guessing.

A digital-camera photo is `digital` / `camera`. A scanned print is `print` / `flatbed_scanner`. A scanned negative is `film_negative` / `film_scanner`. A photo of a print taken with a phone is `print` / `camera_rephoto`.

## colour_class

- `colour`: a normal colour image, including old colour prints that have faded or shifted toward red, magenta, yellow or cyan. A faded colour print still shows different hues in different areas (skin, sky, clothes, foliage).
- `mono`: black-and-white or greyscale, with neutral grey tones. A slight overall cast from scanning is still `mono` if every area is the same neutral grey.
- `toned`: a monochrome image with one consistent tint across all tones: sepia or brown, selenium purple-brown, cyanotype blue. The whole picture is one hue that only varies in lightness; there are no separate colours for different objects. A colour photo of brown things (wood, a sunset) is `colour`.
- `unknown`: you cannot tell, for example a near-colourless faded colour print versus a toned print.

## confidence

For each of `medium`, `digitiser` and `colour_class`, give a number from 0 to 1 for how sure you are of your answer. Use about 0.9 or more only when the cues are clear, 0.5 to 0.8 when they lean one way, and below 0.5 when it is a guess. When you would be guessing, answer `unknown` with a low confidence instead of picking a likely-sounding value.

## rotate_cw

Scans and old files are sometimes stored sideways or upside down. Give how many degrees the image must be turned clockwise to be upright as the photographer intended, judging by the sky, the ground, people's heads and feet, buildings and any text: `0` if it is already upright, or `90`, `180` or `270`. When there is no up (a texture, a top-down shot), answer `0`.
