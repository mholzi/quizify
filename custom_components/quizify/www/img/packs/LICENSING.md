# Pack image licensing policy

Quizify ships every pack image inside the repository, and HACS copies the whole
repository into every Home Assistant that installs the integration. An image in
this tree is therefore redistributed worldwide, on the author's name, to people
who never looked at its licence. That is the standard the rows in each
`*/credits.md` have to meet.

The MIT `LICENSE` at the repository root covers the code. It does not cover
these images; each one keeps the licence recorded in its `credits.md` row.

## The rule

Each row in a `credits.md` table carries a **`Commons template`** column: the
licence template(s) that actually appear on the source file record, copied
verbatim, in backticks. Not the phrase a search result showed, not
`extmetadata.LicenseShortName`, and not a summary — the template names.

`tests/test_pack_image_licences_795.py` parses those tables and checks every
row against three named sets:

| Set | Meaning |
|---|---|
| `WORLDWIDE_SAFE` | the term has run everywhere, or the rights holder released the work: `PD-old`, `PD-old-100`, `PD-old-auto-expired`, `PD-old-auto-1923`, `PD-self`, `CC0`, … |
| `US_FEDERAL_ACCEPTED` | works of the US federal government: `PD-USGov*`, `NASA`, `PD-NASA`. Accepted, with the caveat below. |
| `NEUTRAL` | wrappers and single-country tags that neither prove nor disprove worldwide status: `PD-Art`, `PD-scan`, `PD-old-auto` (it computes a term and may not have expired), `PD-France`, `PD-Uruguay`, … |

A row passes when **at least one** of its templates is in `WORLDWIDE_SAFE` or
`US_FEDERAL_ACCEPTED`, and **none** of them is in `NOT_WORLDWIDE`. A template
the test has never seen fails too, so a new licence basis cannot enter the tree
without somebody classifying it.

`NOT_WORLDWIDE` is the explicit fail list: anything `PD-US-*`, `PD-US`,
`PD-Pre1964`, `PD-NYWT&S`, `PD-1996`, any `cc-by-*`, and the two Commons
warning tags `Not PD Germany` and `Urheberrechtlich geschützt`.

## Why the old check was not a check

Until v1.16.0 the gate was three `in` tests against the whole file: the image
name appears somewhere, the string `Public domain` appears somewhere, and the
string `file record` appears somewhere. It passed a file in which every row but
one was CC BY, and it could not tell `PD-old-100` from `PD-US-not renewed`
because both render as "Public domain". Five bad rows shipped under it —
#739, #793 and the three in #794 — and the test stayed green through all of
them. That is #795.

## The US federal caveat (#739)

17 U.S.C. §105 puts works of the US federal government outside copyright *in
the United States*. It says nothing about anywhere else, and other countries do
not automatically follow it. The project accepts `PD-USGov*` and NASA imagery
anyway, deliberately: the agencies publish these pictures for reuse, they are
reused everywhere without incident, and dropping them would take most of the
science, tech, nature and geography sets with them. The decision is recorded
here so it is a decision and not an oversight.

## Quarantine

`UNDER_REVIEW` in the test lists the rows that do **not** pass and are still on
disk, each with the reason. They are marked ⚠️ in their `credits.md` and are
tracked by [#817](https://github.com/mholzi/quizify/issues/817). The list is allowed to shrink and nothing may be
added to it without an issue; a file that leaves the quarantine has to leave it
because it was replaced or removed, not because the row was re-worded.

## Adding an image

1. Open the **file record**, not the search result. On Commons that is the
   `File:` page; read the wikitext, not the rendered licence box.
2. Copy the licence template names into the `Commons template` column.
3. If the templates are not in `WORLDWIDE_SAFE` or `US_FEDERAL_ACCEPTED`, pick
   a different picture or make the question a text question. Do not add to the
   quarantine.
4. Run `pytest tests/test_pack_image_licences_795.py`.
