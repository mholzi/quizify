# World Cup — image credits

Every row records the **licence template on the Commons file record** — the
template, not the phrase a search result showed. The policy those templates are
checked against is in [`../LICENSING.md`](../LICENSING.md), and
`tests/test_pack_image_licences_795.py` enforces it row by row.

| File | Subject | Creator / holder | Source | Licence | Commons template | Verified by |
|---|---|---|---|---|---|---|
| `estadio-centenario.webp` | The Estadio Centenario, Montevideo, as it stands today | Wikimedia user *Da dinges*, released into the public domain | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Estadio_centenario_1.JPG) | Public domain, released by the photographer | `PD-self` | licence templates read from the file record wikitext |

## `jules-rimet-trophy.webp` left this folder in v1.16.0 (#793)

The row recorded creator "—" and licence "Public domain", and the file header
said no attribution was legally required. The record
(`File:Jules_Rimet_Cup.jpg`) is a Flickr photograph taken on 13 May 2008 by
*Reindertot* and licensed `{{Licensed-PD|PD-old-auto-expired|{{cc-by-2.0}}|deathyear=1953}}`:
the public-domain half applies to Abel Lafleur's sculpture, the photograph
itself is **CC BY 2.0**. CC BY requires the photographer credit and the licence
notice to travel with every copy, and the integration was shipping it into
every HACS install with neither. The file also sat in
`Category:Undeleted in 2026`, so its status had been contested.

Rather than add a caption to the game to carry an attribution nobody had asked
for, the picture came out and the question is now a text question.

## Three more pictures left this folder in v1.17.0 (#817)

The 1950 poster scan, Ghiggia's goal and the 1930 Uruguay squad all rested on a
national tag — `{{Arquivo Nacional PD-license|URAA}}`, `{{PD-AR-Photo}}`,
`{{PD-Uruguay-anon}}` — or, in two of the three, on the US restoration tag
`{{PD-1996}}` alongside it. Each establishes a status in one country, and this
repository is copied into installs in every country.

Football photography of the first four tournaments is held almost entirely by
South American state archives, and their releases carry the same national tags.
The searches run for #817 turned up no photograph of the 1950 poster, of the
deciding goal or of the 1930 champions on any record with a worldwide expiry
tag. So the three pictures came out and their questions are text questions, as
`steamboat-willie.webp` and `jules-rimet-trophy.webp` were in v1.16.0:

* the poster question now asks which country hosted the 1950 tournament;
* the Maracanã question now asks who scored the goal that decided it;
* the squad question now asks which country won the first World Cup.

## The one picture left, and why it is a modern one

`estadio-centenario.webp` replaces the 1930 aerial view of the same ground,
which the Centro de Fotografía de Montevideo released as `{{PD-Uruguay}}` and
nothing else. Its question does not need a 1930 photograph — it asks what the
stadium was built for — so a present-day photograph of the Centenario answers
it just as well, and one exists that its photographer put into the public
domain outright (`{{PD-self}}`). That is a stronger basis than any period
photograph of this tournament has.

## Edited, and why

Nothing in this folder is cropped any more. The two crops recorded here before
— the burned-in Spanish caption on the Maracanã print and the
**"ARQUIVO NACIONAL"** stamp on the poster scan — went with the pictures.

## Sizes

1100 px on the long edge, WebP at quality 80, 99 KB for the folder's single
picture. It was rendered at 340 px — the width the card gets on a 390 px
phone — and looked at before being kept.
