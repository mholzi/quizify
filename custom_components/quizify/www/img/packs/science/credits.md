# Science — image credits

Every row records the **licence template on the Commons file record** — the
template, not the phrase a search result showed. The policy those templates are
checked against is in [`../LICENSING.md`](../LICENSING.md), and
`tests/test_pack_image_licences_795.py` enforces it row by row.

| File | Subject | Creator / agency | Source | Licence | Commons template | Verified by |
|---|---|---|---|---|---|---|
| `first-x-ray.webp` | "Hand mit Ringen" — the first medical X-ray, 22 December 1895 | Wilhelm Conrad Röntgen | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:First_medical_X-ray_by_Wilhelm_R%C3%B6ntgen_of_his_wife_Anna_Bertha_Ludwig%27s_hand_-_18951222.jpg) | Public domain, expired worldwide | `PD-old-100-expired` | licence templates read from the file record wikitext |
| `einstein-portrait.webp` | Albert Einstein, 1921 | Ferdinand Schmutzer (d. 1928) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Einstein_1921_by_F_Schmutzer_-_restoration.jpg) | Public domain, expired worldwide | `PD-Austria`, `PD-old-auto-expired` | same |
| `marie-curie.webp` | Marie Curie, c. 1920 | Henri Manuel (d. 1947) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Marie_Curie_c1920.jpg) | Public domain, expired worldwide | `PD-old-auto-1923` | same |
| `pillars-of-creation.webp` | The Pillars of Creation in the Eagle Nebula, 2014 | NASA, ESA and the Hubble Heritage Team (STScI/AURA) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Pillars_of_creation_2014_HST_WFC3-UVIS_full-res_denoised.jpg) | Public domain as a US federal work — see the caveat in `../LICENSING.md` | `PD-USGov-NASA` | same |
| `hooke-flea.webp` | The flea from Robert Hooke's *Micrographia*, 1665 | Robert Hooke | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:HookeFlea01.jpg) | Public domain, expired worldwide | `PD-old-100` | same |

## The Einstein portrait was swapped in v1.16.0 (#794)

The set used to carry `File:Albert_Einstein_Head.jpg`, the 1947 Orren Jack
Turner portrait, credited as plain "Public domain". The record licences it
`{{PD-old-auto-1964|deathyear=1968}}` and adds `{{Urheberrechtlich geschützt}}`
— the Commons tag for "still protected in Germany, Austria and Switzerland".
Turner died in 1968, so it is protected in every 70-years-pma country until the
end of 2038.

It was replaced by Ferdinand Schmutzer's 1921 portrait, the one taken in the
year of the Nobel Prize the question's fun fact is about. Schmutzer died in
1928; the record carries `{{PD-old-auto-expired|1928}}`, which is expiry, not a
US technicality. The question text did not have to change.

## Edited, and why

* **`first-x-ray.webp`** — the plate carries Röntgen's handwritten caption
  "Hand mit Ringen" along the top and the stamp of the Würzburg physics
  institute along the bottom. Both were cropped away: a German-speaking player
  gets the subject handed to them by the caption, and the stamp names the town
  the discoverer worked in. Same reason the Taj Mahal photochrom lost its
  printed title in the geography set.

## Sizes

1100 px on the long edge, WebP at quality 80, 37–144 KB each, 428 KB for the
folder. Each one was rendered at 340 px — the width the card gets on a 390 px
phone — and looked at before being kept. The two portraits and the flea
engraving hold up plainly; the X-ray needed the crop before the hand read as a
hand at that size; the Pillars are large enough in frame to survive it.

## Spanish

The Spanish pack of this theme asks the same five pictures (#554). No new
files, no new licence work — only question text.
