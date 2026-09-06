# History — image credits

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
| `wright-first-flight.webp` ⚠️ | The first powered flight, Kitty Hawk, 17 December 1903 | John T. Daniels | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:First_flight2.jpg) | Public domain (PD-old) | `PD-US-expired` | `extmetadata.LicenseShortName == "Public domain"` on the file record |
| `migrant-mother.webp` | "Migrant Mother", Nipomo, California, 1936 | Dorothea Lange, Farm Security Administration | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Lange-MigrantMother02.jpg) | Public domain (PD-USGov) | `PD-USGov-FSA`, `PD-old-auto` | same |
| `lincoln-portrait.webp` | Abraham Lincoln, matte collodion print, November 1863 | Alexander Gardner | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Abraham_Lincoln_O-77_matte_collodion_print.jpg) | Public domain (PD-old) | `PD-scan`, `PD-old-auto-1923` | same |
| `titanic-southampton.webp` | RMS Titanic departing Southampton, 10 April 1912 | Francis Godolphin Osbourne Stuart | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:RMS_Titanic_3.jpg) | Public domain (PD-old) | `PD-Scan`, `PD-old-auto-expired` | same |
| `brandenburg-gate-1989.webp` | The wall and the crowd at the Brandenburg Gate, December 1989 | SSGT F. Lee Corkran, US Department of Defense | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:BrandenburgerTorDezember1989.jpg) | Public domain (PD-USGov) | `PD-USGov` | same |

## Dropped, and why

* **"West and East Germans at the Brandenburg Gate in 1989"** — the canonical
  image of people standing on the wall, and the first result for every search
  phrased around the fall of the wall. The file record says CC BY-SA 3.0
  (unknown photographer, reproduction by a Wikipedia contributor), so it cannot
  ship. The DoD photograph from December 1989 covers the same subject and is
  PD-USGov; it shows the wall, the gate and the crowd, only from outside the
  crowd rather than on top of it.

## Checked, because the file name is not evidence

The Titanic photograph is the one trap this subject sets: many pictures sold as
Titanic are her sister ship Olympic, which is near-identical. The Commons file
record gives the description "RMS Titanic departing Southampton on April 10,
1912" with a matching date field, which is the same standard the nature set
applied when a "Monarch Butterfly" photograph turned out to show a viceroy.

## Sizes

1100 px on the long edge, WebP at quality 80, 41–87 KB each, 339 KB for the
folder. Each one was also rendered at 340 px — the width the card actually gets
on a 390 px phone — and looked at before being kept, because a picture that is
not recognisable at that size is not a picture question. All five carry their
subject at that width: a portrait, a face, a ship in profile, a biplane against
empty sand, and a gate behind a painted wall.

## Spanish

The Spanish pack of this theme asks the same five pictures (#554). No new
files, no new licence work — only question text.
