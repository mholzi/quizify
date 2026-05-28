# Review: Animals & Nature (en)

Reviewed on 2026-05-27. 50 questions across 2 chunks. Fixes + deletes applied + re-verified.

## Summary

### After fixes

- ✅ **Clean**: 40 (was 32 + 8 newly fixed-and-clean)
- ⚠️ **Concerns**: 0 cleanly-flagged remaining + 2 from re-verify (nat_en_011 land-animal-vs-blue-whale distractor inconsistency; nat_en_023 "first known" framing)
- 🚨 **Fix needed**: 0
- 🗑️ **Deleted**: 8 — pack reduced from 50 to **42 questions**

### Before fixes

- ✅ Clean: 32
- ⚠️ Concerns: 13
- 🚨 Fix needed: 5

## Fixes applied (10)

| ID | What changed | Re-verify |
|---|---|---|
| nat_en_003 | Answer "Up to 500 years" → "Up to 400 years" (better-supported upper estimate per 2016 Nielsen et al. study, 392±120 years). | ✅ ok |
| nat_en_011 | Question scoped: "Which animal" → "Which **land animal** has the longest pregnancy?" — disambiguates against frilled shark (~3.5 yrs). | ⚠️ concern — blue whale distractor still says "(12 months)" but blue whale isn't a land animal. Distractor should swap. |
| nat_en_020 | Question reframed: "oldest known living organism" → "oldest known individual non-clonal tree" — disambiguates against Pando aspen / Posidonia seagrass clones. | ✅ ok |
| nat_en_021 | "for months" → "for **weeks**" — matches the fun fact and the actual ~1-week reality. | ✅ ok |
| nat_en_023 | Distractor "Squid" → "Goldfish" (squid also has blue copper-based blood, removed the secondary-correct). Question reframed to "first known to have blue, copper-based blood". | ⚠️ concern — "first known" framing is historically loose (molluscs described earlier). |
| nat_en_028 | Fun fact wording: breathing via "gill-like organs on their hind legs" → "pleopodal lungs on the underside of their abdomen" (anatomically accurate). | ✅ ok |
| nat_en_034 | Distractor phrasing tightened (was telegraphing answer with explicit leg counts in option text). Now generic "common garden millipede" / "giant African millipede". | ✅ ok |
| nat_en_035 | Question scoped: "Which animal can regrow its own tail?" → "Which animal can regrow **entire limbs, organs, and parts of its brain**?" — disambiguates against gecko (tail only). | ✅ ok |
| nat_en_043 | Fun fact "10 stomachs" → removed (overstated); now "complex digestive tract with multiple paired caeca for storing blood meals". | ✅ ok |
| nat_en_047 | Distractor "Ray" → "Moray eel" (electric rays also hunt with electric fields; removed secondary-correct). Question reframed to "shocks of up to 860 volts". | ✅ ok |

## Deletions (8)

| ID | Reason |
|---|---|
| nat_en_002 | "Only mammal that cannot jump" — hippos also can't jump; multiple defensible correct answers among elephant/hippo/rhino. |
| nat_en_010 | "How much DNA do humans share with bananas?" — 50% and 60% are both widely cited; the question is inherently ambiguous about what's measured (genome-wide identity vs homologous genes). |
| nat_en_016 | "Loudest animal" — depends on underwater-vs-air dB framing; sperm whale (~230 dB underwater) competes with pistol shrimp. No clean single correct answer. |
| nat_en_017 | "How do homing pigeons navigate?" — pigeons use multi-modal navigation (magnetic, olfactory, solar). "Smell" distractor is partly defensible; magnetite-beak hypothesis itself scientifically disputed. |
| nat_en_018 | "Largest brain-to-body ratio" — contested across small ants, tree shrews, certain fish; no clean winner. |
| nat_en_033 | "Over 1 trillion scents" — viral claim with weak scientific backing; no defensible single correct figure. |
| nat_en_039 | "Three eyelids per eye" — common across many birds (incl. owls) and reptiles (incl. crocodiles); not unique to camels. |
| nat_en_049 | Conflates rhino beetle with dung beetle (the 1,141× record belongs to *Onthophagus taurus*); "Hercules bee" distractor is a fictional species; pack's own question #5 already credits the dung beetle for this. |

## Re-verification

10 fixed questions re-reviewed:
- ✅ **Clean now**: 8
- ⚠️ **Still concerns**: 2 (nat_en_011 blue-whale-as-land-animal distractor; nat_en_023 historical "first known" framing)
- 🚨 **Still fix_needed**: 0

8 deletions are gone (not re-verified).

## Next step on user side

`git diff custom_components/quizify/questions/animals-nature.json` — 10 question edits + 8 deletions visible. Test `test_each_category_has_50_questions` will fail (pack now at 42). Either backfill 8 new questions or relax that test.
