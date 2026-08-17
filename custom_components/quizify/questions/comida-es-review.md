# Review — `comida-es.json`

Reviewed 2026-08-18, all 160 questions in seven parallel chunks of 22–23,
against factual correctness, distractor quality, fun-fact accuracy, clarity,
difficulty calibration and — the sixth criterion for a translated pack —
whether the Spanish is idiomatic and grammatical.

## Result

| | count |
|---|---|
| Questions reviewed | 160 |
| `fix_needed` | 8 |
| `concern` | 44 |
| `ok` | 108 |

All eight `fix_needed` were fixed, along with every `concern` that touched a
fact. What was deliberately left is listed at the end.

## The defects worth naming

**A definition that defined the wrong thing.** `com_es_006` asked what makes an
olive oil *virgen extra* and answered "juice obtained by mechanical means only"
— which is the definition of *virgen*. The extra grade additionally requires low
free acidity and a tasting panel that finds no defect at all. Both now appear in
the answer.

**A missing adverb that made a distractor right.** `com_es_009` asked what ginger
*is* and marked "underground stem". In everyday Spanish ginger is sold and named
as a root, so the distractor was defensible. The question now says
*botánicamente*, exactly as the peanut question two items earlier already did.

**A "the only X" that is not.** Vanilla was called the only orchid with an edible
fruit. Several *Vanilla* species are traded. It now says the only one grown at
scale for its fruit.

**A tautology and a distractor that was also a cause.** `com_es_057` asked why
mayonnaise splits and answered "because the emulsion breaks" — which is what
splitting *is* — while offering "the oil was cold" as a wrong answer, though
temperature shock genuinely does split mayonnaise. The question now asks what
usually causes it and answers: the oil went in too fast.

**A question whose own answer denied its premise.** `com_es_096` asked what curry
"contains" in India and answered that no single curry exists. Worse, it was the
only option not starting with *Siempre*, so the format alone gave it away. It now
asks what is true of curry in India, with three parallel options.

**A distractor that was a real technique.** Under the heading *pasteurización en
frío*, UV treatment is marketed as exactly that — so `com_es_135` had two
defensible answers. The question now names high-pressure processing directly.

**Two more:** the *hervir* vs *escalfar* item ignored the most visible
difference (shell or no shell) and now asks about temperature explicitly; the
malt-whisky item had "the colour of the bottle" as a distractor and confused
*single malt* (one distillery) with malt whisky (one grain).

## Calibration

Nineteen labels moved, almost all downward: a question with two absurd
distractors is answerable by elimination whatever its subject. The pack sits at
**56 easy / 89 medium / 15 hard**, against roughly 52/73/35 for its German and
English siblings. The hard bucket is genuinely thinner here — food trivia has
fewer facts that survive the "specialist recall" bar without becoming
unanswerable.

## Left alone on purpose

- **`com_es_167`, `169`, `170`** — the estimate questions are translations of the
  German and English originals. The reviewer flagged two imprecisions that exist
  identically in all three languages: sucrose caramelises (≈160 °C) *before* it
  melts (≈186 °C), so "below that it merely melts" has the order backwards; and
  ten litres of milk per kilo understates a long-cured cheese. Fixing them in
  Spanish alone would leave three packs disagreeing about the same fact. **This
  belongs in a separate change covering `essen-de`, `food-en` and `comida-es`
  together.**
- **Softened rather than dropped**: the pewter-plate explanation for Europe
  fearing tomatoes, the India-voyage origin of hoppy beer, and honey found edible
  in Egyptian tombs are all repeated far more often than they are evidenced. Each
  now says so instead of losing a good item.
- **`com_es_034`** (resting meat) keeps the conventional "the juices
  redistribute" reading. Modern kitchen science attributes more of the effect to
  cooling and falling fibre pressure, but the observable claim — less juice on
  the board — is correct, and the precise mechanism does not fit an answer
  option.
