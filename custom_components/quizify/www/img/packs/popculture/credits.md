# Pop Culture — image credits

Every row records the **licence template on the Commons file record** — the
template, not the phrase a search result showed. The policy those templates are
checked against is in [`../LICENSING.md`](../LICENSING.md), and
`tests/test_pack_image_licences_795.py` enforces it row by row.

| File | Subject | Creator / holder | Source | Licence | Commons template | Verified by |
|---|---|---|---|---|---|---|
| `voyage-dans-la-lune.webp` | The rocket in the moon's eye, *Le Voyage dans la Lune*, 1902 | Georges Méliès (d. 1938) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Le_voyage_dans_la_lune_drawing.jpg) | Public domain, expired worldwide | `PD-old-auto-expired` | licence templates read from the file record wikitext |
| `charlie-chaplin.webp` | Charlie Chaplin, studio portrait, c. 1915 | Albert Witzel (d. 1929), Witzel Studios, Los Angeles | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Charlie_Chaplin,_by_Witzel_Studios,_LA.jpg) | Public domain, expired worldwide | `PD-old-auto-expired` | same |
| `nosferatu.webp` | Max Schreck as Count Orlok, *Nosferatu — Eine Symphonie des Grauens*, 1922 | Prana-Film / F. W. Murnau | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Max_Schreck_as_Count_Orlok_in_Nosferatu_%E2%80%93_Eine_Symphonie_des_Grauens_(1922).jpg) | Public domain, expired worldwide | `PD-old-auto-expired` | same |
| `caligari-poster.webp` | Poster for *Das Cabinet des Dr. Caligari*, 1920 | Ledl Bernhard (Rudolf Ledl and Fritz Bernhard, d. 1945) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Das_Kabinett_des_Doktor_Caligari_1920_Poster.jpg) | Public domain, expired worldwide | `PD-old-auto-expired` | same |

## Three pictures left this folder in v1.16.0

All three were credited as plain "Public domain" and all three were public
domain in the United States only — the country neither the author nor most of
the users are in.

* **`steamboat-willie.webp`** (#739) — the 1928 film entered the US public
  domain in 2024. The Commons record carries `{{Urheberrechtlich geschützt}}`
  alongside the expiry tag: still protected in Germany, Austria and
  Switzerland, where Ub Iwerks (d. 1971) sets the term until the end of 2041.
  The question is now a text question, which is what #739 asked for.
* **`marilyn-monroe.webp`** (#794) — `{{PD-Pre1964}}`, a redirect to
  `{{PD-US-not renewed}}`. Frank Powolny (d. 1986) is named on the record, so
  the photograph is protected in the EU until the end of 2056. Replaced by the
  Nosferatu still.
* **`we-can-do-it.webp`** (#794) — `{{PD-US-no notice}}` followed by an
  explicit `{{Not PD Germany}}`. J. Howard Miller (d. 1985) is the named
  artist, so the poster is protected in the EU until the end of 2055. Replaced
  by the Caligari poster.

## `charlie-chaplin.webp` got a different photograph in v1.17.0 (#817)

The Hartsook portrait rested on `{{PD-US-expired}}` alone: the US term has run,
and the record names no photographer and no death year, so nothing on it showed
the picture was free in Germany. The Witzel Studios portrait of the same
subject from the same year carries `{{PD-old-auto-expired|deathyear=1929}}` —
Albert Witzel died in 1929, so the term ran out worldwide at the end of 1999.

Every Chaplin portrait on Commons with a worldwide expiry tag is a signed
publicity print, and the signature reads "Charlie Chaplin" across the sitter's
jacket. That is the answer to the question, so the sheet was cropped to the
head and shoulders; the `Witzel LA` studio credit in the lower left went with
it. The 1922 *Jour de Paye* poster (`PD-Art|PD-old-auto-expired`, Raymond
Pallier d. 1943) would have been the alternative and is 421 px wide, which is
below what a card 340 px wide at DPR 2 can carry.

## The wall, and where the door is

Pop culture was flagged in #554 as the hardest subject, alongside music, and
for the obvious reason: film stills, publicity photographs and television
imagery from the last seventy years are rights-managed without exception. The
free material is early cinema, where the makers have been dead long enough for
the term to have run everywhere. Every picture here is from 1902 to 1922.
Anything more recent stays a text question.

## Edited, and why

**`voyage-dans-la-lune.webp`** — the sheet was captioned, in French, *"LE
VOYAGE DANS LA LUNE — EN PLEIN DANS L'ŒIL"* under the drawing. The question
asks which film the image comes from, so the caption was the answer. Cropped
off.

`charlie-chaplin.webp` keeps the small *Hartsook Photo* studio credit at the
lower left. It is part of the original photograph rather than a later overlay,
it names nothing the question asks for, and cropping it would have taken the
subject's shoulder with it.

`caligari-poster.webp` keeps the printed title and the cast list. Its question
asks which film movement the poster belongs to, and the sheet does not answer
that.

## Sizes

Up to 1100 px on the long edge; the Nosferatu still keeps its native 705×900.
WebP at quality 80, 31–140 KB each, 316 KB for the folder. Each was rendered at
340 px — the width the card gets on a 390 px phone — and looked at before being
kept.

## Spanish

The Spanish pack of this theme asks the same pictures (#554). No new files, no
new licence work — only question text.
