# Sport — image credits

Every row records the **licence template on the Commons file record** — the
template, not the phrase a search result showed. The policy those templates are
checked against is in [`../LICENSING.md`](../LICENSING.md), and
`tests/test_pack_image_licences_795.py` enforces it row by row.

| File | Subject | Creator / holder | Source | Licence | Commons template | Verified by |
|---|---|---|---|---|---|---|
| `jesse-owens-1936.webp` | Jesse Owens at the Berlin Olympics, 1936 | *Le Miroir des sports*, issue of 11 August 1936 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Jesse_Owens_%C3%A0_Berlin,_JO_de_1936.jpg) | Public domain, expired worldwide | `PD-Art`, `PD-old-70` | licence templates read from the file record wikitext |
| `babe-ruth.webp` | Babe Ruth warming up, 1915 | Paul Thompson (d. 1940) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Babe_Ruth_by_Paul_Thompson,_1915.jpg) | Public domain, author dead more than 70 years | `PD-old` | same |
| `tour-de-france-1903.webp` | Maurice Garin after winning the first Tour de France, 1903; Léon Georget, second, on the right | Unknown (anonymous, 1903) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Maurice_Garin_Tour_1903.jpg) | Public domain, published before 1923 and 70 years past | `PD-old-70-1923` | same |
| `suzanne-lenglen.webp` | Suzanne Lenglen serving, 1922 | Agence Rol (Bibliothèque nationale de France) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Suzanne_Lenglen_1922_(instant).jpg) | Public domain, author dead more than 70 years | `PD-old` | same |

## `muhammad-ali.webp` left this folder in v1.16.0 (#794)

The row said "Public domain". The record's actual basis is `{{PD-NYWT&S}}`, the
Library of Congress deed of gift for the New York World-Telegram and Sun
collection — a grant of rights, not an expiry, and one whose reach outside the
United States the record does not state. The photograph came out and the
question is now a text question.

## Three photographs were swapped in v1.17.0 (#817)

All three of the rows this folder had open rested on a status in one country:
`{{PD-US}}` says the US term has run and nothing more, and `{{PD-1996}}` is the
URAA restoration tag, which is a statement about US law. None was re-credited
on the grounds that the work was *probably* free elsewhere — that reasoning is
what put five bad rows into v1.15.0. Each one got a different photograph whose
record carries an expiry that does not stop at a border.

* **`jesse-owens-1936.webp`** — was a National Archives print of the long jump
  tagged `{{PD-US}}`. Now a press photograph of Owens at Berlin from
  *Le Miroir des sports* of 11 August 1936, `{{PD-Art|PD-old-70}}`: an
  anonymous French press photograph published in 1936, whose 70-year term ran
  in 2007. It shows him waiting at the start rather than in the air, so the
  question no longer asks which event it is; it asks who he is.
* **`babe-ruth.webp`** — was Conlon's 1916 batting photograph on `{{PD-US}}`.
  Conlon died in 1945 and the record does not say so, which is exactly the gap.
  The 1915 photograph by Paul Thompson carries `{{PD-old}}` and names its
  photographer through a Commons creator page that gives his death year, 1940.
* **`tour-de-france-1920.webp` → `tour-de-france-1903.webp`** — the Agence Rol
  photograph of Philippe Thys is `{{PD-France}}` plus `{{PD-1996}}`, and the
  BnF's own copies of the other Rol plates from that day carry no better tag.
  The replacement is an anonymous photograph of Maurice Garin after the first
  Tour in 1903, `{{PD-old-70-1923}}`. The question moved with it, from 1920 to
  1903.

Two records that would have been easier were left alone: `File:Garin03winner.jpg`
(`{{PD-old}}`, no author, no source, no date on the record) and
`File:Philippe Thys 1920 Tour de France.jpg` (`{{PD-France}}` again). A bare
`{{PD-old}}` on a record that names nobody is an expiry claim nothing on the
page supports.

## Dropped, and why

* **`Jesse Owens.png`**, the portrait that heads most search results — CC BY-SA
  4.0 on the file record. The National Archives photograph of the long jump is
  in the public domain in the United States and, for a picture question,
  better: it shows the event rather than the face, so the question can ask what
  is happening instead of who it is.

## Edited, and why

Both Agence Rol pictures are scans of glass plates and carried the plate
furniture into the frame:

* the Lenglen serve had **25194** scratched into the lower left corner —
  cropped away.

It leaked no answer, but it read as a scanning artefact on a card that is
supposed to show a photograph.

The 1915 Ruth photograph is a scan of an auction lot: it had the print's white
mount on all four sides and an **"Imaged by Heritage Auctions, HA.com"** line
along the bottom. Both were cropped off, for the reason the Potato Eaters
watermark was rejected in the food set — licence-clear is not ship-clear.

## Sizes

1100 px on the long edge, WebP at quality 80, 38–130 KB each, 368 KB for the
folder. Each was rendered at 340 px — the width the card gets on a 390 px
phone — and looked at before being kept. The two wide crowd scenes are the
tightest call of the set: in both, the subject (a flower-covered bicycle, a
player at full stretch on a tennis court) survives at that width, which is what
the question rests on.

## Spanish

The Spanish pack of this theme asks the same pictures (#554). No new files, no
new licence work — only question text.
