"""The pack-image licence gate (#795).

The gate this replaces was three substring tests against a whole file: the
image name appears somewhere, ``"Public domain"`` appears somewhere,
``"file record"`` appears somewhere. It could not tell one row from another and
it could not tell ``PD-old-100`` from ``PD-US-not renewed``, because both
render as "Public domain" in the Commons metadata. Five bad rows shipped under
it — a CC BY 2.0 photograph with no attribution (#793), three files that are
public domain in the United States only, two of them carrying an explicit
"not PD in Germany" tag (#794), and the first of that class (#739) — and the
suite stayed green through all of them.

So the check is now per row and mechanical. Every ``credits.md`` table carries
a ``Commons template`` column holding the licence templates copied off the
source file record, and every template is looked up in a named set. A template
nobody has classified fails, which is the part that makes the gate hold: a new
licence basis cannot enter the tree without a human putting it in a set.

The policy the sets encode is written out in
``custom_components/quizify/www/img/packs/LICENSING.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKS = REPO / "custom_components" / "quizify" / "www" / "img" / "packs"

# The term has run in every country, or the rights holder gave the work away.
# "expired" and "1923" in a Commons template name are load-bearing: they assert
# the term has actually run, where a bare computing template only promises to
# work one out.
WORLDWIDE_SAFE = frozenset(
    {
        "PD-old",
        "PD-old-70",
        "PD-old-70-1923",
        "PD-old-100",
        "PD-old-100-1923",
        "PD-old-100-expired",
        "PD-old-auto-1923",
        "PD-old-auto-expired",
        "PD-author",
        "PD-self",
        "CC0",
        "Cc-zero",
        "cc-zero",
    }
)

# Works of the US federal government. US-only by statute (17 U.S.C. §105), and
# accepted anyway — deliberately, with the reasoning in LICENSING.md. #739
# raised it; this set is what "the project accepts the caveat" looks like in
# code, rather than the phrase "Public domain" hiding it.
US_FEDERAL_ACCEPTED = frozenset(
    {
        "NASA",
        "PD-NASA",
        "PD-USGov",
        "PD-USGov-FSA",
        "PD-USGov-FWS",
        "PD-USGov-Military-Army",
        "PD-USGov-NASA",
        "PD-USGov-NASA-AP",
        "PD-USGov-NPS",
        "PD-USGov-USDA-NAL-Pomological-Watercolors",
    }
)

# Neither proves nor disproves worldwide status, so it may sit in a row without
# carrying it. Wrappers (PD-Art, PD-scan, self) take a base and pass it
# through. Single-country tags say the term has run in one place. PD-old-auto
# is here rather than above because it computes from a death year and the
# result may be decades away — Migrant Mother carries it with 1965.
NEUTRAL = frozenset(
    {
        "Arquivo Nacional PD-license",
        "PD-AR-Photo",
        "PD-Art",
        "PD-Austria",
        "PD-France",
        "PD-Russia-expired",
        "PD-Scan",
        "PD-Uruguay",
        "PD-Uruguay-anon",
        "PD-old-auto",
        "PD-scan",
        "self",
    }
)

# The explicit fail list from #795. A row carrying any of these fails whatever
# else is on it: a US-only expiry, a US restoration tag, a deed of gift rather
# than an expiry, an attribution licence, or a Commons warning tag that says in
# so many words that the work is still protected where we live.
NOT_WORLDWIDE = frozenset(
    {
        "CC-BY-2.0",
        "Licensed-PD",
        "Not PD Germany",
        "PD-1996",
        "PD-NYWT&S",
        "PD-Pre1964",
        "PD-US",
        "PD-US-expired",
        "PD-US-no notice",
        "PD-US-not renewed",
        "PD-old-auto-1964",
        "Urheberrechtlich geschützt",
        "cc-by-2.0",
        "cc-by-3.0",
        "cc-by-4.0",
        "cc-by-sa-3.0",
        "cc-by-sa-4.0",
    }
)

KNOWN = WORLDWIDE_SAFE | US_FEDERAL_ACCEPTED | NEUTRAL | NOT_WORLDWIDE

# Rows that fail the rule and are still on disk. Each one has to be replaced or
# removed; none may be argued away by rewording its row. The list is allowed to
# shrink, and nothing goes into it without an issue — which is why it is a
# mapping and not a set.
#
# It is empty. v1.16.0 fixed the six rows #793, #794 and #739 named, and
# v1.17.0 fixed the eleven #817 named — those eleven are pinned in
# ``QUARANTINE_817`` below, with what each record actually said and what
# happened to it.
UNDER_REVIEW: dict[str, str] = {}
QUARANTINE_ISSUE = 817


def verdict(templates: tuple[str, ...] | list[str]) -> str:
    """The whole rule, in one place: ``"pass"`` or why not.

    Split out from the tests so the historical rows below can be run through
    exactly the code that guards the tree, rather than through a paraphrase of
    it.
    """
    if not templates:
        return "no licence template recorded"
    unknown = [t for t in templates if t not in KNOWN]
    if unknown:
        return f"unclassified template(s) {unknown}"
    bad = [t for t in templates if t in NOT_WORLDWIDE]
    if bad:
        return f"{bad} is not a worldwide basis"
    if not any(t in WORLDWIDE_SAFE | US_FEDERAL_ACCEPTED for t in templates):
        return (
            f"none of {list(templates)} establishes public domain "
            "outside a single country"
        )
    return "pass"


_ROW = re.compile(r"^\|(?P<cells>.+)\|\s*$")
_FILE_CELL = re.compile(r"`(?P<name>[^`]+\.webp)`")
_TEMPLATE = re.compile(r"`(?P<template>[^`]+)`")


class CreditRow:
    """One picture's row, as the table records it."""

    def __init__(
        self,
        folder: str,
        image: str,
        templates: tuple[str, ...],
        source: str = "",
    ) -> None:
        self.folder = folder
        self.image = image
        self.templates = templates
        self.source = source

    @property
    def key(self) -> str:
        return f"{self.folder}/{self.image}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CreditRow {self.key} {list(self.templates)}>"


def _cells(line: str) -> list[str] | None:
    match = _ROW.match(line.rstrip("\n"))
    if not match:
        return None
    return [c.strip() for c in match.group("cells").split("|")]


def parse_credits(path: Path) -> list[CreditRow]:
    """Read one ``credits.md`` table.

    Column positions are looked up from the header rather than hard-coded, so a
    folder is free to call its third column "Creator / agency" or "Creator" and
    a new column in the middle does not silently shift the licence one over.
    """
    folder = path.parent.name
    header: list[str] | None = None
    file_col = template_col = source_col = -1
    rows: list[CreditRow] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        cells = _cells(line)
        if cells is None:
            continue
        if header is None:
            if cells[0] != "File":
                continue
            header = cells
            assert "Commons template" in header, (
                f"{path} has no 'Commons template' column"
            )
            file_col = header.index("File")
            template_col = header.index("Commons template")
            source_col = header.index("Source") if "Source" in header else -1
            continue
        if set("".join(cells)) <= {"-", ":"}:
            continue
        name = _FILE_CELL.search(cells[file_col])
        if not name:
            continue
        cell = cells[template_col]
        templates = tuple(m.group("template") for m in _TEMPLATE.finditer(cell))
        source = cells[source_col] if source_col >= 0 else ""
        rows.append(CreditRow(folder, name.group("name"), templates, source))

    assert header is not None, f"{path} has no credits table"
    return rows


CREDIT_FILES = sorted(PACKS.glob("*/credits.md"))
ALL_ROWS = [row for path in CREDIT_FILES for row in parse_credits(path)]
ROW_IDS = [row.key for row in ALL_ROWS]

assert CREDIT_FILES, "no credits.md found — the gate would pass vacuously"
assert ALL_ROWS, "no credit rows parsed — the gate would pass vacuously"


@pytest.mark.parametrize("row", ALL_ROWS, ids=ROW_IDS)
def test_every_row_records_at_least_one_licence_template(row: CreditRow) -> None:
    """An empty cell is the failure the old gate had: nothing recorded, nothing
    checkable, and a green suite."""
    assert row.templates, f"{row.key}: the Commons template column is empty"


@pytest.mark.parametrize("row", ALL_ROWS, ids=ROW_IDS)
def test_every_template_has_been_classified(row: CreditRow) -> None:
    """A template none of the four sets knows about fails. Otherwise a new
    licence basis enters the tree the moment somebody types it into a row."""
    unknown = [t for t in row.templates if t not in KNOWN]
    assert not unknown, (
        f"{row.key}: unclassified licence template(s) {unknown}. "
        "Put each one in WORLDWIDE_SAFE, US_FEDERAL_ACCEPTED, NEUTRAL or "
        "NOT_WORLDWIDE in this file, having read what it actually asserts."
    )


@pytest.mark.parametrize("row", ALL_ROWS, ids=ROW_IDS)
def test_no_row_ships_on_a_licence_that_stops_at_a_border(row: CreditRow) -> None:
    """The rule, per row: at least one template that carries the work, and
    nothing on the fail list.

    HACS copies this repository into every install, so "public domain in the
    United States" is not a licence to ship from Germany.
    """
    why = verdict(row.templates)

    if row.key in UNDER_REVIEW:
        assert why != "pass", (
            f"{row.key} passes the licence rule but is still listed in "
            "UNDER_REVIEW — take it out of the quarantine."
        )
        pytest.xfail(
            f"{row.key}: {UNDER_REVIEW[row.key]} (#{QUARANTINE_ISSUE})"
        )

    assert why == "pass", (
        f"{row.key}: {why} — "
        f"see custom_components/quizify/www/img/packs/{row.folder}/credits.md"
    )


def test_the_quarantine_lists_only_files_that_exist() -> None:
    """A stale entry would keep a fixed row from ever being re-checked."""
    on_disk = {row.key for row in ALL_ROWS}
    assert set(UNDER_REVIEW) <= on_disk, (
        f"UNDER_REVIEW names rows that are gone: {sorted(set(UNDER_REVIEW) - on_disk)}"
    )


def test_every_quarantined_row_is_marked_in_its_credits_file() -> None:
    """The quarantine has to be visible where somebody reads the provenance,
    not only in a test nobody opens."""
    for key in sorted(UNDER_REVIEW):
        folder, image = key.split("/")
        text = (PACKS / folder / "credits.md").read_text(encoding="utf-8")
        assert f"`{image}` ⚠️" in text, (
            f"{key} is quarantined but not marked ⚠️ in credits.md"
        )


def test_the_sets_do_not_overlap() -> None:
    """Two sets claiming the same template would make the rule depend on the
    order the code happens to test them in."""
    sets = {
        "WORLDWIDE_SAFE": WORLDWIDE_SAFE,
        "US_FEDERAL_ACCEPTED": US_FEDERAL_ACCEPTED,
        "NEUTRAL": NEUTRAL,
        "NOT_WORLDWIDE": NOT_WORLDWIDE,
    }
    names = list(sets)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            overlap = sets[left] & sets[right]
            assert not overlap, f"{left} and {right} both claim {sorted(overlap)}"


def test_every_shipped_image_has_a_row() -> None:
    """The reverse of the per-row check: a file on disk that no table mentions
    would never be looked at by any of the tests above."""
    for path in sorted(PACKS.glob("*/credits.md")):
        recorded = {row.image for row in parse_credits(path)}
        on_disk = {p.name for p in path.parent.glob("*.webp")}
        assert on_disk == recorded, (
            f"{path.parent.name}: {sorted(on_disk ^ recorded)} is in one of "
            "the folder and the credits table but not the other"
        )


def test_the_policy_is_written_down() -> None:
    """The sets above are a rule; LICENSING.md is why it is that rule. Without
    it the next person reads a list of template names and guesses."""
    policy = (PACKS / "LICENSING.md").read_text(encoding="utf-8")
    terms = ("Commons template", "US_FEDERAL_ACCEPTED", "UNDER_REVIEW", "17 U.S.C.")
    for term in terms:
        assert term in policy, f"LICENSING.md does not explain {term}"


# The six rows v1.15.0 shipped, with the templates their file records actually
# carried. The old gate passed every one of them: each folder contained the
# string "Public domain" somewhere, which was the whole test. Kept as a fixture
# so the gate can never quietly loosen back to letting them through.
ROWS_THAT_SHIPPED_IN_V1_15_0 = {
    "worldcup/jules-rimet-trophy.webp": (
        "Licensed-PD",
        "PD-old-auto-expired",
        "cc-by-2.0",
    ),
    "popculture/we-can-do-it.webp": ("PD-US-no notice", "Not PD Germany"),
    "popculture/marilyn-monroe.webp": ("PD-Pre1964",),
    "popculture/steamboat-willie.webp": (
        "PD-old-auto-expired",
        "Urheberrechtlich geschützt",
    ),
    "science/einstein-portrait.webp": (
        "PD-old-auto-1964",
        "Urheberrechtlich geschützt",
    ),
    "sport/muhammad-ali.webp": ("PD-NYWT&S",),
}


@pytest.mark.parametrize(
    "key,templates",
    sorted(ROWS_THAT_SHIPPED_IN_V1_15_0.items()),
    ids=sorted(ROWS_THAT_SHIPPED_IN_V1_15_0),
)
def test_the_rows_that_shipped_in_v1_15_0_are_rejected(
    key: str, templates: tuple[str, ...]
) -> None:
    """#793, #794 and #739, run through the gate that should have caught them.

    Note steamboat-willie: its expiry tag is on the allowlist and it still
    fails, because the record also carries the Commons tag that says the work
    is protected in Germany. A rule that only looked for one good template
    would have waved it through a second time.
    """
    assert verdict(templates) != "pass", f"{key} would ship again on {list(templates)}"


@pytest.mark.parametrize(
    "key,templates",
    sorted(ROWS_THAT_SHIPPED_IN_V1_15_0.items()),
    ids=sorted(ROWS_THAT_SHIPPED_IN_V1_15_0),
)
def test_the_rejected_rows_are_gone_from_the_tree(
    key: str, templates: tuple[str, ...]
) -> None:
    """Rejecting them in the abstract is not the same as their being off disk.

    Five of the six files were deleted. ``einstein-portrait.webp`` kept its
    name and got a different photograph behind it — the 1921 Schmutzer portrait
    in place of the 1947 Turner one — so for that one the check is that the row
    no longer rests on the old templates.
    """
    folder, image = key.split("/")
    path = PACKS / folder / image
    if not path.exists():
        return
    rows = {row.image: row for row in parse_credits(PACKS / folder / "credits.md")}
    assert set(rows[image].templates).isdisjoint(templates), (
        f"{key} still records {templates}"
    )


def test_no_row_is_marked_quarantined_without_being_quarantined() -> None:
    """The ⚠️ in a table and ``UNDER_REVIEW`` have to agree in both directions.

    ``test_every_quarantined_row_is_marked_in_its_credits_file`` checks one
    direction and goes vacuous the moment the quarantine empties. This checks
    the other, and does not.
    """
    for path in CREDIT_FILES:
        text = path.read_text(encoding="utf-8")
        marked = {
            f"{path.parent.name}/{m.group(1)}"
            for m in re.finditer(r"`([^`]+\.webp)` ⚠️", text)
        }
        stray = marked - set(UNDER_REVIEW)
        assert not stray, (
            f"{path} marks {sorted(stray)} ⚠️ but UNDER_REVIEW does not list them"
        )


# ---------------------------------------------------------------------------
# #817: the eleven rows the per-row gate found, and what became of each.
#
# The check the gate replaced "greps for a phrase" — so these facts live in the
# repository as data rather than in a pull request nobody reads again. Each
# entry records what the file record *actually* carried when it was read, the
# decision taken, and, where a picture is still shipped, the record it now
# rests on and the templates that record carries. The tests below read the
# credits tables and compare; a row that is quietly re-worded fails here.
# ---------------------------------------------------------------------------

REMOVED = "removed"       # picture out of the tree, question is a text question
REBASED = "rebased"       # same picture, a record that states a worldwide term
REPLACED = "replaced"     # a different picture, free worldwide


class Resolution:
    """One quarantined row, decided."""

    def __init__(
        self,
        was: tuple[str, ...],
        why: str,
        outcome: str,
        now_key: str | None = None,
        now: tuple[str, ...] = (),
        record: str = "",
    ) -> None:
        self.was = was
        self.why = why
        self.outcome = outcome
        self.now_key = now_key
        self.now = now
        self.record = record


QUARANTINE_817 = {
    "history/wright-first-flight.webp": Resolution(
        was=("PD-US-expired",),
        why="US term only; the record names no death year",
        outcome=REBASED,
        now_key="history/wright-first-flight.webp",
        now=("PD-old-auto-expired",),
        record="https://commons.wikimedia.org/wiki/File:First_flight3.jpg",
    ),
    "nature/puffin.webp": Resolution(
        was=("PD-USGov-FWS", "CC-BY-2.0"),
        why="a USFWS work the Flickr review also tagged CC BY 2.0; "
        "we ship no attribution",
        outcome=REPLACED,
        now_key="nature/puffin.webp",
        now=("PD-USGov-FWS",),
        record="https://commons.wikimedia.org/wiki/File:Puffins_(22706591919).jpg",
    ),
    "popculture/charlie-chaplin.webp": Resolution(
        was=("PD-US-expired",),
        why="US term only; the Hartsook photographer is not identified",
        outcome=REPLACED,
        now_key="popculture/charlie-chaplin.webp",
        now=("PD-old-auto-expired",),
        record="https://commons.wikimedia.org/wiki/"
        "File:Charlie_Chaplin,_by_Witzel_Studios,_LA.jpg",
    ),
    "sport/jesse-owens-1936.webp": Resolution(
        was=("PD-US",),
        why="a National Archives photograph tagged US-expired rather than PD-USGov",
        outcome=REPLACED,
        now_key="sport/jesse-owens-1936.webp",
        now=("PD-Art", "PD-old-70"),
        record="https://commons.wikimedia.org/wiki/"
        "File:Jesse_Owens_%C3%A0_Berlin,_JO_de_1936.jpg",
    ),
    "sport/babe-ruth.webp": Resolution(
        was=("PD-US",),
        why="Conlon d. 1945, so almost certainly free in the EU — "
        "the record does not say it",
        outcome=REPLACED,
        now_key="sport/babe-ruth.webp",
        now=("PD-old",),
        record="https://commons.wikimedia.org/wiki/"
        "File:Babe_Ruth_by_Paul_Thompson,_1915.jpg",
    ),
    "sport/tour-de-france-1920.webp": Resolution(
        was=("PD-France", "PD-1996"),
        why="French expiry plus the US restoration tag; no worldwide expiry tag",
        outcome=REPLACED,
        now_key="sport/tour-de-france-1903.webp",
        now=("PD-old-70-1923",),
        record="https://commons.wikimedia.org/wiki/File:Maurice_Garin_Tour_1903.jpg",
    ),
    "tech/ford-assembly-line.webp": Resolution(
        was=("PD-US",),
        why="US term only on a 1913 corporate photograph",
        outcome=REPLACED,
        now_key="tech/ford-assembly-line.webp",
        now=("PD-old-70-1923",),
        record="https://commons.wikimedia.org/wiki/"
        "File:Assembly_line_at_the_Ford_Motor_Company%27s_Highland_Park_plant_"
        "LCCN2011661021.jpg",
    ),
    "worldcup/centenario-1930.webp": Resolution(
        was=("PD-Uruguay",),
        why="Uruguayan expiry only",
        outcome=REPLACED,
        now_key="worldcup/estadio-centenario.webp",
        now=("PD-self",),
        record="https://commons.wikimedia.org/wiki/File:Estadio_centenario_1.JPG",
    ),
    "worldcup/world-cup-poster-1950.webp": Resolution(
        was=("Arquivo Nacional PD-license",),
        why="a Brazilian archive release, URAA-qualified",
        outcome=REMOVED,
    ),
    "worldcup/maracana-1950.webp": Resolution(
        was=("PD-AR-Photo", "PD-1996"),
        why="Argentine expiry plus the US restoration tag",
        outcome=REMOVED,
    ),
    "worldcup/uruguay-1930.webp": Resolution(
        was=("PD-Uruguay-anon", "PD-US-expired"),
        why="free in Uruguay and the US; nothing on the record covers the EU",
        outcome=REMOVED,
    ),
}

Q817_IDS = sorted(QUARANTINE_817)
Q817_ITEMS = [(key, QUARANTINE_817[key]) for key in Q817_IDS]


def _rows_by_key() -> dict[str, CreditRow]:
    return {row.key: row for row in ALL_ROWS}


@pytest.mark.parametrize("key,res", Q817_ITEMS, ids=Q817_IDS)
def test_the_licence_that_failed_still_fails(key: str, res: Resolution) -> None:
    """Run each quarantined row's real templates back through the gate.

    The point is not that they failed once — it is that the sets above can
    never be loosened until they pass again without this turning red.
    """
    assert verdict(res.was) != "pass", f"{key} would ship again on {list(res.was)}"


@pytest.mark.parametrize("key,res", Q817_ITEMS, ids=Q817_IDS)
def test_no_row_still_rests_on_the_licence_that_failed(
    key: str, res: Resolution
) -> None:
    """A row may not keep its old basis under a new heading.

    Removing the name from ``UNDER_REVIEW`` is not a fix; neither is moving the
    row to a different table. So the row that ships now may not carry the exact
    set that failed, and may not carry any of the templates that disqualified
    it. The puffin is why the second half is narrower than the first: its
    record always said ``PD-USGov-FWS`` and that was never the problem — the
    ``CC-BY-2.0`` beside it was.
    """
    row = _rows_by_key().get(res.now_key or key)
    if row is None:
        return
    assert set(row.templates) != set(res.was), (
        f"{row.key} still records exactly {list(res.was)}"
    )
    disqualifying = {template for template in res.was if template in NOT_WORLDWIDE}
    assert set(row.templates).isdisjoint(disqualifying), (
        f"{row.key} still records {sorted(set(row.templates) & disqualifying)}"
    )


@pytest.mark.parametrize("key,res", Q817_ITEMS, ids=Q817_IDS)
def test_a_removed_picture_is_gone_from_disk_and_from_the_table(
    key: str, res: Resolution
) -> None:
    """"Turn the question into a text question" has to mean the file left.

    A picture that stays on disk keeps being redistributed by HACS whether a
    question points at it or not.
    """
    if res.outcome != REMOVED:
        return
    folder, image = key.split("/")
    assert not (PACKS / folder / image).exists(), f"{key} was removed but is on disk"
    assert key not in _rows_by_key(), f"{key} was removed but still has a credits row"


@pytest.mark.parametrize("key,res", Q817_ITEMS, ids=Q817_IDS)
def test_a_kept_picture_records_the_licence_this_issue_established(
    key: str, res: Resolution
) -> None:
    """The facts, pinned: the file record read, and the templates it carries.

    This is the check the old gate could not make. It does not look for the
    word "Public domain" anywhere in the file — it reads the row for one named
    image and compares the templates and the source URL against what was
    actually read off Commons for #817.
    """
    if res.outcome == REMOVED:
        return
    assert res.now_key is not None and res.now and res.record
    folder, image = res.now_key.split("/")
    assert (PACKS / folder / image).exists(), f"{res.now_key} is not on disk"

    row = _rows_by_key().get(res.now_key)
    assert row is not None, f"{res.now_key} has no credits row"
    assert row.templates == res.now, (
        f"{res.now_key} records {list(row.templates)}, "
        f"but the file record read for #817 carries {list(res.now)}"
    )
    assert verdict(row.templates) == "pass", (
        f"{res.now_key}: {verdict(row.templates)}"
    )
    assert res.record in row.source, (
        f"{res.now_key} does not cite {res.record} as its source"
    )


def test_the_quarantine_this_issue_inherited_is_empty() -> None:
    """#817 closes when the eleven are decided, not when the list is deleted.

    ``UNDER_REVIEW`` going empty is only meaningful next to a table that says
    what happened to each name that used to be in it — which is the table
    above, and which the tests either side of this one check against the tree.
    """
    left = set(UNDER_REVIEW) & set(QUARANTINE_817)
    assert not left, f"still quarantined: {sorted(left)}"
    assert len(QUARANTINE_817) == 11, "#817 named eleven rows"


def test_the_outcomes_are_the_ones_the_policy_describes() -> None:
    """``LICENSING.md`` states the shape of the #817 sweep in words.

    Words drift from the table they describe. This is the one place the two are
    compared, so a later row that changes outcome has to change the policy text
    with it.
    """
    counts: dict[str, int] = {}
    for res in QUARANTINE_817.values():
        counts[res.outcome] = counts.get(res.outcome, 0) + 1
    assert counts == {REBASED: 1, REPLACED: 7, REMOVED: 3}, counts

    policy = (PACKS / "LICENSING.md").read_text(encoding="utf-8")
    assert "one picture moved to a Commons record" in policy
    assert "seven were swapped" in policy
    assert "three came out of the tree" in policy
