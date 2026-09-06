# Animals & Nature — image credits

Every row records the **licence template on the Commons file record** — the
template, not the phrase a search result showed. The policy those templates are
checked against is in [`../LICENSING.md`](../LICENSING.md), and
`tests/test_pack_image_licences_795.py` enforces it row by row.

⚠️ marks a row whose template is **not** worldwide safe. It is quarantined in
that test and tracked by
[#817](https://github.com/mholzi/quizify/issues/817); it has to be replaced or
removed, not re-worded.

| File | Subject | Creator / agency | Source | Licence | Commons template | Verified by |
|---|---|---|---|---|---|---|
| `puffin.webp` ⚠️ | Atlantic puffin carrying fish | U.S. Fish and Wildlife Service | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Atlantic_Puffin_(8574372061).jpg) | Public domain (PD-USGov) | `PD-USGov-FWS`, `CC-BY-2.0` | `extmetadata.LicenseShortName == "Public domain"` on the file record |
| `bald-eagle.webp` | Bald eagle at Ottawa National Wildlife Refuge | U.S. Fish and Wildlife Service | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Usfws-bald-eagle-ottawa-refuge.jpg) | Public domain (PD-USGov) | `PD-USGov`, `PD-author` | same |
| `snowflake.webp` | Plate of snow-crystal photomicrographs, c. 1902 | Wilson A. Bentley | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:SnowflakesWilsonBentley.jpg) | Public domain (PD-old) | `PD-old-auto-1923` | same |
| `hurricane-iss.webp` | The eye of Hurricane Florence from the ISS, 2018 | NASA | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Staring_Down_Hurricane_Florence.jpg) | Public domain (PD-NASA) | `NASA` | same |
| `aurora-iss.webp` | Aurora australis above Antarctica from the ISS | NASA | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:ISS-52_Aurora_australis_above_Antarctica.jpg) | Public domain (PD-NASA) | `PD-USGov-NASA-AP` | same |

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
