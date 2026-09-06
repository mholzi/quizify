# Geography — image credits

Every row records the **licence template on the Commons file record** — the
template, not the phrase a search result showed. The policy those templates are
checked against is in [`../LICENSING.md`](../LICENSING.md), and
`tests/test_pack_image_licences_795.py` enforces it row by row.

| File | Subject | Creator / agency | Source | Licence | Commons template | Verified by |
|---|---|---|---|---|---|---|
| `yellowstone-spring.webp` | Grand Prismatic Spring, Yellowstone, from the air | Jim Peaco, National Park Service | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Grand_prismatic_spring.jpg) | Public domain (PD-USGov) | `PD-USGov-NPS` | `extmetadata.LicenseShortName == "Public domain"` on the file record |
| `statue-of-liberty.webp` | Statue of Liberty and Liberty Island, 2017 | Carol M. Highsmith, Library of Congress | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:HIghsmith_2017_LOC_Statue_of_Liberty_NY_Harbor.jpg) | CC0 (public-domain dedication) | `cc-zero` | same |
| `strait-of-gibraltar.webp` | The Strait of Gibraltar from the ISS, 2024 | NASA Johnson Space Center (iss071e414110) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:The_Strait_of_Gibraltar_(iss071e414110).jpg) | Public domain (PD-NASA) | `PD-USGov-NASA` | same |
| `nile-delta-night.webp` | The Nile and its delta at night from the ISS, 2010 | ISS Expedition 25 crew (NASA) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Nile_River_Delta_at_Night_cropped.JPG) | Public domain (PD-NASA) | `PD-USGov-NASA` | same |
| `taj-mahal.webp` | Taj Mahal, Agra — photochrom, c. 1890 | Library of Congress (PPOC), [LCCN 95505064](https://lccn.loc.gov/95505064) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Agra,_Taj_Mahal_LCCN95505064.jpg) | Public domain (PD-old) | `PD-old-70-1923` | same |

## Edited, and why

* **`taj-mahal.webp`** — the bottom strip of the original print carries the
  caption `20029. P.Z. — AGRA TAJ MAHAL` burned into the image. A picture
  question that names its own answer is not a picture question, so the strip is
  cropped off.
* **`statue-of-liberty.webp`** — cropped in from the left and bottom. The full
  frame is an aerial with a lot of empty harbour; on a 390 px phone the statue
  was a speck.

## Dropped, and why

* **Machu Picchu** (Hiram Bingham, 1912 — licence fine, PD-old) — the only
  freely licensed views of the site are Bingham's black-and-white expedition
  photographs, taken while the ruins were still half overgrown. At 340 px it
  reads as "some Andean terraces", not as the place everybody has seen on a
  postcard. Recognition is the whole mechanic of a picture question, so it went.
* **Uluru, Machu Picchu (modern), Victoria Falls, the Grand Canyon, Sydney,
  Christ the Redeemer** — all CC BY-SA. The famous landmarks of the southern
  hemisphere are, almost without exception, photographed by people who keep
  their attribution. That is why this set leans on space agencies, US federal
  photographers and 19th-century prints.

## Sizes

1100 px on the long edge, WebP at quality 72–80, 51–137 KB each, 483 KB for the
set. Each one was rendered at 340 px — the width the card actually gets on a
390 px phone — and looked at before being kept.

## Spanish

The Spanish pack of this theme asks the same five pictures (#554). No new
files, no new licence work — only question text.
