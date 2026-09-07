# Animals & Nature — image credits

Every row records the **licence template on the Commons file record** — the
template, not the phrase a search result showed. The policy those templates are
checked against is in [`../LICENSING.md`](../LICENSING.md), and
`tests/test_pack_image_licences_795.py` enforces it row by row.

| File | Subject | Creator / agency | Source | Licence | Commons template | Verified by |
|---|---|---|---|---|---|---|
| `puffin.webp` | Atlantic puffins at Maine Coastal Islands National Wildlife Refuge | U.S. Fish and Wildlife Service | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Puffins_(22706591919).jpg) | Public domain (PD-USGov) | `PD-USGov-FWS` | licence templates read from the file record wikitext |
| `bald-eagle.webp` | Bald eagle at Ottawa National Wildlife Refuge | U.S. Fish and Wildlife Service | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Usfws-bald-eagle-ottawa-refuge.jpg) | Public domain (PD-USGov) | `PD-USGov`, `PD-author` | same |
| `snowflake.webp` | Plate of snow-crystal photomicrographs, c. 1902 | Wilson A. Bentley | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:SnowflakesWilsonBentley.jpg) | Public domain (PD-old) | `PD-old-auto-1923` | same |
| `hurricane-iss.webp` | The eye of Hurricane Florence from the ISS, 2018 | NASA | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Staring_Down_Hurricane_Florence.jpg) | Public domain (PD-NASA) | `NASA` | same |
| `aurora-iss.webp` | Aurora australis above Antarctica from the ISS | NASA | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:ISS-52_Aurora_australis_above_Antarctica.jpg) | Public domain (PD-NASA) | `PD-USGov-NASA-AP` | same |

## The photograph this row replaced (#817)

The old picture — a single puffin with a beakful of fish, from the USFWS
Northeast Region Flickr stream — carried **two** licences that cannot both be
right: `{{CC-BY-2.0}}` with a passed Flickr review, and `{{PD-USGov-FWS}}`. If
the CC BY half applies, every copy has to travel with the photographer credit
and the licence notice, and this repository shipped neither. Every picture in
that Flickr stream carries the same pair, so the replacement came from
elsewhere: `File:Puffins (22706591919).jpg`, credited on the record as
"Photo: USFWS" and tagged `{{PD-USGov-FWS}}` and nothing else. The Flickr
account that posted it is the National Park Service's climate-change stream;
both agencies are US federal, and `PD-USGov-*` is the caveat this project has
already taken deliberately (see [`../LICENSING.md`](../LICENSING.md)).

## Dropped, and why

* **A "Monarch Butterfly" photograph** (PD-USGov, licence fine) — the wing in
  the picture carries a black line across the hindwing veins, which is the
  viceroy's marking, not the monarch's. The file name says monarch. A picture
  question whose answer depends on the species cannot rest on a name that the
  photograph contradicts, so the subject was swapped for the puffin.
* **A ginkgo leaf photograph** — CC BY-SA 3.0. It came back from a search for
  public-domain images; the file record said otherwise.

## Sizes

900–1200 px on the long edge, WebP at quality 80, 53–108 KB each. Each one was
also rendered at 340 px — the width the card actually gets on a 390 px phone —
and looked at before being kept, because a picture that is not recognisable at
that size is not a picture question.

## Spanish

The Spanish pack of this theme asks the same five pictures (#554). No new
files, no new licence work — only question text.
