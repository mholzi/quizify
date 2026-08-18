# Review — `tecnologia-es.json`

Reviewed 2026-08-18, all 160 questions in seven parallel chunks of 22–23,
against factual correctness, distractor quality, fun-fact accuracy, clarity,
difficulty calibration and — the sixth criterion for a translated pack —
whether the Spanish is idiomatic and grammatical.

## Result

| | count |
|---|---|
| Questions reviewed | 160 |
| `fix_needed` | 9 |
| `concern` | 46 |
| `ok` | 105 |

All nine `fix_needed` were fixed, along with every `concern` that touched a
fact. What was deliberately left is listed at the end.

## The defects worth naming

**A distractor that many historians would mark correct.** `tec_es_002` asked who
wrote the first algorithm meant for a machine, answered Ada Lovelace, and put
Charles Babbage in the same list as a wrong answer. The attribution is genuinely
disputed and Babbage is the leading rival claim, so the item punished the
better-read player. He is now replaced by John von Neumann; the fun fact still
credits his analytical engine.

**A question that inverted its own premise.** `tec_es_022` asked what problem the
Y2K bug *solved*. Y2K was the problem, not the fix. It now asks what the effect
consisted of.

**Proprietary is a licence, not a hidden source.** `tec_es_029` equated
proprietary software with unpublished source. Source-available proprietary
software exists — Unreal Engine among others. The question now asks about
*código cerrado*, which is what the answer actually describes.

**An entropy claim that does not survive arithmetic.** `tec_es_043` claimed four
random common words beat eight "twisted" characters. Four Diceware words give
about 51.7 bits, eight random printable characters about 52.4 — a tie, not a
win. The claim only holds against eight characters a *human* picked. The question
now asks what current guidance prioritises, and the fun fact cites what NIST
actually dropped in 2017 (forced rotation, composition rules) instead of running
a comparison that does not hold.

**The microwave myth, repeated by our own fun fact.** `tec_es_062` said food
heats "de dentro afuera". Microwaves penetrate one to two centimetres; heating
starts at the outside and travels inward by conduction. Rewritten, with the
stirring advice that follows from it.

**A distractor that describes a real phone.** `tec_es_075` marked "compares a
photo of the finger" as wrong, but optical under-display readers do exactly that
and are common on mid-range Android. Replaced.

**Three orders of magnitude.** `tec_es_090` had the hard-disk head flying at
*micras* from the platter. Micrometres were 1970s figures; modern flying height
is a few nanometres. Corrected.

**A cost comparison the wrong way round.** `tec_es_118` said slab track is "more
expensive to maintain" than ballast. It is the opposite: slab track costs more to
build and less to maintain. Corrected, and the stem now asks for the *principal*
function, because drainage and load spreading are both genuine ballast functions.

**A definition question answered by an opinion.** `tec_es_146` asked what
algorithmic neutrality *is* and marked "a contested assumption" correct, while
"shows everything chronologically" — the popular definition — counted as wrong.
The player had to guess the author's framing. It now asks what a recommender
always does with what it shows: it orders it by some criterion. The point about
chronological order being itself a criterion survives in the fun fact.

## Fun facts corrected without touching the question

Nineteen fun facts carried a claim that was outdated, overstated or internally
inconsistent with its own question:

- **Data-centre cooling** (`028`) — "a large share of consumption" was true at
  PUE ≈ 2; hyperscalers now run 1.1–1.2, i.e. 10–20 %.
- **Arpanet's first message** (`031`) — called "the inaugural message of the
  internet" in a question whose whole point is that Arpanet preceded the internet.
- **The at sign** (`034`) — "the only key that appeared in no name" overstates
  Tomlinson; he said it could not appear *in a username*.
- **Wi-Fi** (`037`) — called IEEE 802.11 a "sigla". It is a standard number.
- **LEDs** (`066`) — "almost without passing through heat" is wrong; LEDs convert
  roughly 30–50 % of input power to light and still need heatsinks.
- **Sensor size** (`079`) — more light per pixel only holds at equal pixel count.
- **Staging** (`094`) — "the only way" to orbit; SSTO is possible, just useless.
- **Geostationary** (`096`) — dishes in a region point at *their* satellite, not
  all at the same one.
- **Maglev** (`101`) — in EMS the levitation magnets are on the vehicle; the track
  carries the linear-motor stator. Now "bobinas y electroimanes".
- **Tower-crane counterweight** (`102`) — most are fixed blocks, not sliding.
- **Locks** (`106`) — "only gravity" is not true of newer locks with pumps and
  water-saving basins. Now scoped to the classic ones.
- **Airbags** (`108`) — the bag is not full "before the person starts moving
  forward"; the occupant is already moving. Now "before the person reaches it".
- **Material fatigue** (`115`) — "los primeros reactores comerciales" reads as
  nuclear reactors in a technology quiz. Now "aviones a reacción". The answer also
  gained *elástico* after "límite".
- **Betamax** (`121`) — "better picture" is contested and was small in practice;
  it now reports the belief rather than asserting the fact. LaserDisc, which also
  lost to VHS and was therefore defensible, was replaced by Video 2000.
- **Telegraph** (`123`) — "the first time a message travelled faster than its
  carrier" ignores Chappe's optical telegraph. Now the real advantage: night and
  fog.
- **Concorde** (`129`) — "measured less inside than a narrow aisle" was garbled;
  it means the cabin was narrower than a single-aisle jet.
- **Gutenberg** (`131`) — "the alloy weighed as much as the invention" reads
  literally as weight. Replaced with what the alloy actually did.
- **Apollo Guidance Computer** (`136`) — the documented nickname is *LOL memory*,
  for the little old ladies who wove it; the free Spanish rendering read like an
  official name.
- **RFID** (`081`) — the fun fact restated the answer almost verbatim. It now adds
  where else the same principle appears.

## Distractors strengthened instead of downgrading the label

Eight questions were marked `hard` but had distractors weak enough to solve by
elimination. Rather than relabel them and thin the hard tier below 10 % of the
pack, the distractors were replaced with plausible-but-false alternatives:

| id | topic | what replaced the give-away |
|---|---|---|
| `014` | quantum computers | "tries every answer at once and picks the right one" — the actual popular misconception |
| `015` | free software | "has no legal owner" — FOSS is copyrighted, which is the point |
| `023` | lossless compression | "reduces any file to a fixed size" |
| `052` | net neutrality | same contracted speed for everyone / stopping state blocking |
| `068` | capacitors | voltage regulation and rectification — two real component roles |
| `095` | rocket landing | engines cannot relight in flight / guidance shuts off at separation |
| `119` | spot welding | continuous-arc welding and cold welding — both real joining methods |
| `135` | Moore's law | Wirth's law, the genuine confusion |

Five more had a distractor that was arguably also correct — `045` (a filename
*is* file metadata), `054` (a mistyped URL *is* commonly called a broken link),
`078` ("lowers ambient volume" is what ANC feels like), `080` (a barcode does
identify what gets priced), `102` (tower cranes do carry base ballast). All five
were replaced.

Four stems were tightened where the wording admitted a second reading: `044`
asked about "two steps" but expected two *factors*; `053` asked what a correct
*copy* is and answered with a whole strategy; `060` promised an etymology with
"en su origen" and delivered a definition; `063` and `083` needed "actual" and
"detecta" respectively. `114`'s stem was ungrammatical Spanish and was rewritten.

## Difficulty

Eight questions labelled above their real difficulty were moved down one notch
(`006`, `007`, `038`, `069`, `079`, `129`, `132`, `136`), and `146` fell to
`medium` once its framing puzzle was removed. Final distribution:

| | easy | medium | hard |
|---|---|---|---|
| count | 59 | 84 | 17 |

## Left deliberately

- **`035` and `024` are mirror images** — bandwidth's correct answer is latency's
  distractor and vice versa. Reviewers flagged the redundancy if they come up
  back to back. Both are good questions and the pack ships 160; the shuffle makes
  adjacency unlikely, and cutting one to avoid it costs more than it saves.
- **`122` (QWERTY)** — the jam-prevention story is historiographically disputed
  (the telegraph-transcription account is the main rival), but it is the answer
  every general-knowledge source gives and both distractors are unambiguously
  false. Left as the standard account.
- **`080` still says a barcode "codifica un número"** — true of EAN/UPC, which is
  what the question shows; Code 128 also carries letters. Scoping the stem to a
  symbology would cost more clarity than the precision buys.
- **The five estimate questions** (`166`–`170`) came back clean on every axis,
  including the arithmetic in their fun facts. `169`'s answer (4 KB) sits near the
  bottom of its 1–1000 KB slider, which is the point: almost everyone guesses high.
