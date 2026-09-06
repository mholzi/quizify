# World Cup — image credits

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
| `centenario-1930.webp` ⚠️ | Estadio Centenario during the 1930 World Cup final, Montevideo | Uruguayan national archive | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Vista_a%C3%A9rea_del_Estadio_Centenario_durante_la_final_del_primer_Campeonato_Mundial_de_F%C3%BAtbol_change.jpg) | Public domain in Uruguay; the record states no term for anywhere else | `PD-Uruguay` | licence templates read from the file record wikitext |
| `world-cup-poster-1950.webp` ⚠️ | Spectators beside the poster for the 1950 tournament, Brazil | Arquivo Nacional (Brazil) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Copa_de_1950,_torcedores_observam_o_cartaz_da_copa.jpg) | Released by the Brazilian national archive; the record's tag is `URAA`-qualified | `Arquivo Nacional PD-license` | same |
| `maracana-1950.webp` ⚠️ | Ghiggia's goal in the deciding match at the Maracanã, 1950 | *El Gráfico* (Argentina) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Gol_ghiggia_vs_brasil.jpg) | Public domain in Argentina; the second tag is the US restoration tag | `PD-AR-Photo`, `PD-1996` | same |
| `uruguay-1930.webp` ⚠️ | The Uruguay squad, world champions 1930 | Unknown (anonymous, Uruguay) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Uruguay_national_football_team_1930.jpg) | Public domain in Uruguay and the US; nothing on the record covers the EU | `PD-Uruguay-anon`, `PD-US-expired` | same |

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

## Every remaining row is open

All four surviving pictures rest on a national tag — Uruguay, Brazil,
Argentina — or on the US restoration tag. Those establish a status in one
country. Football photography of the first four tournaments is where the free
material is, and it is almost entirely held by South American state archives,
so a like-for-like replacement is not a search away. The rows are quarantined
with the issue that tracks them; nothing here is claimed to be worldwide public
domain.

## Edited, and why

* **`maracana-1950.webp`** — the print carried a Spanish caption burned along
  the bottom naming the final, the date **16/7/50** and the Maracanã. The
  question asks what the photograph shows, so the caption was the answer.
  Cropped off.
* **`world-cup-poster-1950.webp`** — an **"ARQUIVO NACIONAL"** stamp sat in the
  top right corner of the scan. Cropped off for the same reason the Potato
  Eaters watermark was rejected in the food set: licence-clear is not
  ship-clear.

## One question that leans on knowledge, not recognition

The squad photograph could in principle be any team. Its question names what
the picture is and asks for the fact around it — which suits a World Cup pack,
where the subject *is* the history. The other three are straight recognition: a
stadium, a poster, a ball in the net.

## Sizes

1100 px on the long edge (the Maracanã print keeps its native 1024 px), WebP at
quality 80, 36–213 KB each, 508 KB for the folder. Each was rendered at 340 px —
the width the card gets on a 390 px phone — and looked at before being kept.
The poster is the one that had to survive it: at card width the lines *IV
CAMPEONATO MUNDIAL DE FUTEBOL* and *BRASIL* are still readable, which is what
its question rests on.
