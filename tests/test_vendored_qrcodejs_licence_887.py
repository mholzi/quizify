"""The vendored qrcodejs copy must carry its licence with it (#887).

MIT has exactly one condition: "The above copyright notice and this permission
notice shall be included in all copies or substantial portions of the Software."
For nine months the only trace of it in this repository was a one-line header
reading ``MIT License`` — a reference to a licence, not the licence. Neither the
copyright line nor the permission text existed anywhere in the tree, so every
HACS install shipped a copy of somebody else's code without the one thing they
asked for.

The fonts already got this right (``www/fonts/DM_Sans-OFL.txt`` and its
sibling). This file holds the vendored JavaScript to the same standard, and
holds the header to naming a version that can be checked — upstream has no
releases and no tags, so the commit SHA *is* the version.

These are file-shape assertions on purpose. Nothing at runtime reads a licence,
which is exactly why nobody noticed it was missing.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR = REPO_ROOT / "custom_components" / "quizify" / "www" / "js" / "vendor"
LIB = VENDOR / "qrcode.min.js"
LICENCE = VENDOR / "qrcodejs-LICENSE.txt"
THIRD_PARTY = REPO_ROOT / "THIRD_PARTY.md"

# The upstream commit the vendored bytes were taken from: master of
# davidshimjs/qrcodejs, unchanged since 2015-11-25.
UPSTREAM_SHA = "04f46c6a0708418cb7b96fc563eacae0fbf77674"


def test_the_licence_text_ships_beside_the_library() -> None:
    """Not in a README, not at the repo root — next to the file it covers.

    HACS copies ``custom_components/quizify/`` and nothing else, so a licence
    kept outside that tree would not reach a single install.
    """
    assert LICENCE.is_file(), (
        f"{LICENCE.relative_to(REPO_ROOT)} is missing — MIT's only condition is "
        "that the notice travels with the copy"
    )


def test_both_notices_mit_asks_for_are_present() -> None:
    """The copyright notice AND the permission notice. Either alone is not it."""
    text = LICENCE.read_text("utf-8")
    assert "Copyright (c) 2012 davidshimjs" in text, (
        "the upstream copyright line is the half that names who is being credited"
    )
    assert "Permission is hereby granted, free of charge" in text
    assert (
        "The above copyright notice and this permission notice shall be included"
        in text
    ), "the permission notice is the condition itself, not boilerplate"
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_the_header_names_a_version_that_can_be_checked() -> None:
    """"unknown version" was avoidable: no releases upstream, but plenty of SHAs.

    A version nobody can resolve makes "is our copy current, and is it what we
    think it is?" unanswerable without diffing 20 KB of minified JavaScript.
    """
    header = LIB.read_text("utf-8").split("\n", 1)[0]
    assert "unknown version" not in header
    assert UPSTREAM_SHA in header, (
        f"the header must name the upstream commit, got: {header!r}"
    )
    assert "qrcodejs-LICENSE.txt" in header, (
        "a reader of the header should not have to guess where the licence is"
    )
    assert "davidshimjs" in header


def test_the_library_below_the_header_is_untouched_upstream() -> None:
    """One added line, and it is a comment.

    If a future edit changes the code itself, the header stops describing what
    is in the file and the SHA above becomes a claim rather than a fact.
    """
    lines = LIB.read_text("utf-8").split("\n")
    assert re.fullmatch(r"/\*.*\*/", lines[0]), "line 1 must be the header comment"
    body = "\n".join(lines[1:])
    assert body.startswith("var QRCode;!function(){")
    assert len(body.encode("utf-8")) == 19927, (
        "the vendored body is no longer byte-identical to upstream "
        f"{UPSTREAM_SHA[:7]} — re-vendor and update the header rather than "
        "editing minified third-party code in place"
    )


def test_third_party_md_is_the_index_and_actually_points_here() -> None:
    """A licence file nobody can find is only marginally better than none."""
    assert THIRD_PARTY.is_file()
    text = THIRD_PARTY.read_text("utf-8")
    assert "qrcodejs-LICENSE.txt" in text
    assert UPSTREAM_SHA in text
    # The fonts are the precedent this follows; the index covers them too.
    assert "DM_Sans-OFL.txt" in text
    assert "JetBrains_Mono-OFL.txt" in text
    assert "THIRD_PARTY.md" in (REPO_ROOT / "README.md").read_text("utf-8"), (
        "the README's License section is where a redistributor looks first"
    )
