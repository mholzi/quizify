# Sport — image credits

Every row records the **licence template on the Commons file record** — the
template, not the phrase a search result showed. The policy those templates are
checked against is in [`../LICENSING.md`](../LICENSING.md), and
`tests/test_pack_image_licences_795.py` enforces it row by row.

⚠️ marks a row whose template is **not** worldwide safe. It is quarantined in
that test and tracked by
[#817](https://github.com/mholzi/quizify/issues/817); it has to be replaced or
removed, not re-worded.

| File | Subject | Creator / holder | Source | Licence | Commons template | Verified by |
|---|---|---|---|---|---|---|
| `jesse-owens-1936.webp` ⚠️ | Jesse Owens in the long jump, Berlin Olympics 1936 | US National Archives | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Photograph_of_Jesse_Owens_at_the_1936_Olympics_in_Berlin,_Germany_-_DPLA_-_4da1d9c3055db6d180835b91c32ce4ec.jpg) | Public domain **in the US only** on the record as it stands | `PD-US` | licence templates read from the file record wikitext |
| `babe-ruth.webp` ⚠️ | Babe Ruth batting, 1916 | Charles M. Conlon (d. 1945) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Babe_Ruth_by_Conlon,_1916.jpeg) | Public domain **in the US only** on the record as it stands | `PD-US` | same |
| `tour-de-france-1920.webp` ⚠️ | Philippe Thys, winner of the 1920 Tour de France, Parc des Princes | Agence Rol (Bibliothèque nationale de France) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:25-07-20,_Tour_de_France_(Philippe)_Thys,_gagnant_-_btv1b53051414t.jpg) | Public domain in France; the second tag is the US restoration tag | `PD-France`, `PD-1996` | same |
| `suzanne-lenglen.webp` | Suzanne Lenglen serving, 1922 | Agence Rol (Bibliothèque nationale de France) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Suzanne_Lenglen_1922_(instant).jpg) | Public domain, author dead more than 70 years | `PD-old` | same |

## `muhammad-ali.webp` left this folder in v1.16.0 (#794)

The row said "Public domain". The record's actual basis is `{{PD-NYWT&S}}`, the
Library of Congress deed of gift for the New York World-Telegram and Sun
collection — a grant of rights, not an expiry, and one whose reach outside the
United States the record does not state. The photograph came out and the
question is now a text question.

## Three rows are still open

`jesse-owens-1936.webp`, `babe-ruth.webp` and `tour-de-france-1920.webp` all
carry templates that establish a status in one country. `{{PD-US}}` says the US
term has run and nothing more; `{{PD-1996}}` is the URAA restoration tag, which
is a statement about US law. Conlon died in 1945 and the Agence Rol pictures
are from 1920, so the underlying works are very likely free in the EU as well —
but "very likely" is what put four bad rows into v1.15.0, so they are
quarantined until a record says it.

## Dropped, and why

* **`Jesse Owens.png`**, the portrait that heads most search results — CC BY-SA
  4.0 on the file record. The National Archives photograph of the long jump is
  in the public domain in the United States and, for a picture question,
  better: it shows the event rather than the face, so the question can ask what
  is happening instead of who it is.

## Edited, and why

Both Agence Rol pictures are scans of glass plates and carried the plate
furniture into the frame:

* the Tour photograph had the negative number **53202** running down the left
  edge and a black plate border on all four sides — cropped away;
* the Lenglen serve had **25194** scratched into the lower left corner — same.

Neither leaked an answer, but both read as scanning artefacts on a card that is
supposed to show a photograph.

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
