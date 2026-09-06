# Technology — image credits

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
| `eniac.webp` | Glen Beck and Betty Snyder programming ENIAC, Ballistic Research Laboratory | US Army | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Eniac.jpg) | Public domain (PD-USGov) | `PD-USGov-Military-Army` | `extmetadata.LicenseShortName == "Public domain"` on the file record |
| `ford-assembly-line.webp` ⚠️ | Ford's moving assembly line, 1913 | Unknown, Ford Motor Company | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Ford_assembly_line_-_1913.jpg) | Public domain (PD-old) | `PD-US` | same |
| `sputnik.webp` | Replica of Sputnik 1, the first artificial satellite | NASA | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Sputnik_asm.jpg) | Public domain (PD-NASA) | `PD-USGov-NASA` | same |
| `punch-card.webp` | An 80-column IBM-style punched card | Arnold Reinhold | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Blue-punch-card-front-horiz.png) | Public domain | `self`, `PD-self` | same |
| `lunar-module.webp` | Apollo 11 lunar module *Eagle* in lunar orbit, 1969 | NASA (Michael Collins) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Apollo_11_Lunar_Module_Eagle_in_landing_configuration_in_lunar_orbit_from_the_Command_and_Service_Module_Columbia.jpg) | Public domain (PD-NASA) | `PD-USGov-NASA` | same |

## Dropped, and why

* **The Enigma machine.** Every freely licensed photograph of one is either
  CC BY-SA (the museum and Bundesarchiv images) or, in the case of the NSA's
  own public-domain exhibit photographs, 450–520 px on the long edge. The pack
  standard is 900–1200 px, and an upscaled 450 px picture on a card that is
  340 px wide at DPR 2 is visibly soft. The subject is worth returning to if a
  larger public-domain photograph turns up.
* **The IBM 350 RAMAC**, the first hard disk — CC BY-SA 2.5 and 360 px wide,
  failing on both counts.

## A note on the set

Two of the five are spacecraft, which is what the licence pool for twentieth-
century technology looks like: NASA releases its photographs into the public
domain and almost nobody else does. They ask different questions — a satellite
and a lander — and the other three are firmly terrestrial.

## Sizes

1100 px on the long edge (Sputnik keeps its native 1094 px), WebP at quality
80, 25–137 KB each, 367 KB for the folder. Each one was rendered at 340 px —
the width the card gets on a 390 px phone — and looked at before being kept.
The punched card is the extreme aspect ratio of the set at 1100×495; at card
width the holes and the printed row along the top edge stay legible, which is
what the question needs.
