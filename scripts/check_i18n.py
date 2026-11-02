#!/usr/bin/env python3
"""Text check for Quizify's three UI languages (#1177).

Reads ``www/i18n/{de,en,es}.json``, the terminology in ``www/i18n/glossary.json``,
the host card's label map (``www/cards/quizify-host-card.js``) and the spoken
phrases in ``game/tts_phrases.py``, and reports what the microcopy rules say is
wrong:

    missing      a key one bundle has and another lacks, or an empty value
    placeholder  the {name}s of a key differ between languages
    glossary     a form the glossary forbids (per language, with exceptions)
    overlength   longer than max(1.6 x en, en + 12), or over the budget in
                 glossary.json -> limits (button 22, pill 18, TV heading 28)
    fragment     keys named ...Before/After/Unit/Suffix/Line1/Line2, and
                 hand-built ...One/...Many plural pairs
    card         a host-card label whose bundle key is missing, or whose
                 English fallback says something else than en.json
    hardcoded    an aria-label / title / placeholder in the HTML without its
                 data-i18n-* twin
    punct        "..." instead of "…", a space before "…", English labels in
                 Title Case, words in capitals
    unused       a key nothing under www/ (or the Python) refers to

Warning mode by default: it prints the findings and a summary line and exits 0.
``--strict`` exits 1 when anything is found, for the day CI makes it an error.

    python3 scripts/check_i18n.py            # report
    python3 scripts/check_i18n.py --strict   # report, fail on findings
    python3 scripts/check_i18n.py --summary  # summary line only
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

LANGS = ("de", "en", "es")
CATEGORIES = (
    "missing",
    "placeholder",
    "glossary",
    "overlength",
    "fragment",
    "card",
    "hardcoded",
    "punct",
    "unused",
)

# Key prefixes completed at runtime — the same list as
# tests/test_dead_i18n_keys_798.py, which names the call site for each.
DYNAMIC_PREFIXES = (
    "errors.",
    "join.refused.",
    "packSubmit.status.",
    "powerups.",
    "difficulties.",
    "connection.",
    "categories.",
    "podium.",
    "highlights.awards.",
    "highlights.details.",
    "game.streakToast",
)

_PLACEHOLDER_RE = re.compile(r"(?<![\w=#])\{\s*(\w+)\s*[,}]")
_PLURAL_HEAD_RE = re.compile(r"\{\s*(\w+)\s*,\s*plural\s*,")
_FRAGMENT_RE = re.compile(r"(Before|After|Unit|Suffix|Line1|Line2)$")
_ATTR_RE = re.compile(
    r"<(?P<tag>[a-zA-Z][\w-]*)(?P<attrs>[^>]*?)>", re.S
)
_HTML_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
_CAPS_WORD_RE = re.compile(r"\b[A-ZÄÖÜÁÉÍÓÚÑ]{3,}\b")


@dataclass
class Finding:
    category: str
    lang: str
    key: str
    message: str

    def line(self) -> str:
        return f"{self.category:<11} {self.lang:<2} {self.key:<34} {self.message}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, category: str, lang: str, key: str, message: str) -> None:
        self.findings.append(Finding(category, lang, key, message))

    def count(self, category: str) -> int:
        return sum(1 for f in self.findings if f.category == category)

    def summary(self) -> str:
        return "summary: " + " · ".join(
            f"{self.count(c)} {c}" for c in CATEGORIES
        )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _leaves(node: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in node.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_leaves(value, name))
        else:
            out[name] = value
    return out


def _load_tts(path: Path) -> dict[str, dict[str, str]]:
    """``_PHRASES`` out of tts_phrases.py, without importing the package."""
    if not path.is_file():
        return {}
    tree = ast.parse(path.read_text("utf-8"))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            target = first.id if isinstance(first, ast.Name) else None
            value = node.value
        if target == "_PHRASES" and value is not None:
            phrases = ast.literal_eval(value)
            return {
                lang: {f"tts.{k}": v for k, v in table.items()}
                for lang, table in phrases.items()
            }
    return {}


def _match(key: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith(".*") and key.startswith(pat[:-1]):
            return True
        if pat == key:
            return True
    return False


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def _close_of(text: str, open_at: int) -> int:
    """Index of the brace closing the one at ``open_at``, or -1."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _branches(body: str) -> list[str]:
    out: list[str] = []
    i = 0
    while True:
        open_at = body.find("{", i)
        if open_at == -1:
            return out
        close = _close_of(body, open_at)
        if close == -1:
            return out
        out.append(body[open_at + 1 : close])
        i = close + 1


def display_text(text: str) -> str:
    """What a template looks like on screen, for measuring: the longest plural
    branch, # as a two-digit count, {param} as a four-letter name."""
    out = text
    for _ in range(10):  # nested plurals expand one level per pass
        m = _PLURAL_HEAD_RE.search(out)
        if not m:
            break
        end = _close_of(out, m.start())
        if end == -1:
            break
        branches = _branches(out[m.end() : end])
        chosen = max(branches, key=len) if branches else ""
        out = out[: m.start()] + chosen + out[end + 1 :]
    out = out.replace("#", "10")
    return re.sub(r"\{\w+\}", "Name", out)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_parity(report: Report, bundles: dict[str, dict[str, str]]) -> None:
    en = bundles["en"]
    for lang in LANGS:
        mine = bundles[lang]
        for key in sorted(set(en) | set(mine)):
            if key not in mine:
                report.add("missing", lang, key, "missing in this language")
            elif not str(mine[key]).strip():
                report.add("missing", lang, key, "empty value")
        if lang == "en":
            continue
        for key in sorted(set(en) & set(mine)):
            a, b = placeholders(en[key]), placeholders(mine[key])
            if a != b:
                report.add(
                    "placeholder", lang, key,
                    f"{{{', '.join(sorted(b))}}} ≠ en {{{', '.join(sorted(a))}}}",
                )


def check_glossary(
    report: Report, texts: dict[str, dict[str, str]], glossary: dict
) -> None:
    for lang, rules in glossary.get("forbidden", {}).items():
        for key, text in sorted(texts.get(lang, {}).items()):
            for rule in rules:
                if "only" in rule and not _match(key, rule["only"]):
                    continue
                if _match(key, rule.get("except", [])):
                    continue
                hit = re.search(rule["pattern"], _prose(text))
                if hit:
                    report.add(
                        "glossary", lang, key,
                        f"{hit.group(0)!r} → {rule['use']}   \"{_short(text)}\"",
                    )


def check_length(
    report: Report, bundles: dict[str, dict[str, str]], glossary: dict
) -> None:
    en = bundles["en"]
    budgets: list[tuple[str, int, list[str]]] = [
        (name, spec["max"], spec["keys"])
        for name, spec in glossary.get("limits", {}).items()
    ]
    for lang in LANGS:
        for key, text in sorted(bundles[lang].items()):
            shown = display_text(text)
            for name, limit, keys in budgets:
                if _match(key, keys) and len(shown) > limit:
                    report.add(
                        "overlength", lang, key,
                        f"{len(shown)} > {limit} ({name})   \"{shown}\"",
                    )
            if lang == "en" or key not in en:
                continue
            base = len(display_text(en[key]))
            if len(shown) > max(1.6 * base, base + 12):
                report.add(
                    "overlength", lang, key,
                    f"{len(shown)} vs en {base}   \"{_short(shown)}\"",
                )


def check_fragments(report: Report, en: dict[str, str]) -> None:
    groups: dict[str, list[str]] = {}
    for key in en:
        m = _FRAGMENT_RE.search(key)
        if m:
            groups.setdefault(key[: m.start()], []).append(key)
    for stem, keys in sorted(groups.items()):
        report.add("fragment", "", stem, "+".join(sorted(keys)))
    for key in sorted(en):
        if key.endswith("One"):
            stem = key[:-3]
            if stem in en or f"{stem}Many" in en:
                report.add("fragment", "", stem, f"{key} — use a plural rule")


def check_card(report: Report, card: Path, en_bundle: dict, bundles: dict) -> None:
    if not card.is_file():
        return
    text = card.read_text("utf-8")
    keys_block = re.search(r"var KEYS = \{(.*?)\n    \};", text, re.S)
    if not keys_block:
        # Still an own label table: every label is drift by definition.
        if "var LABELS" in text:
            report.add("card", "", "LABELS", "the card carries its own label table")
        return
    mapping = dict(re.findall(r"(\w+): '([\w.]+)'", keys_block.group(1)))
    fallback_block = re.search(r"var FALLBACK = \{(.*?)\n    \};", text, re.S)
    fallback = (
        dict(re.findall(r"(\w+): '((?:[^'\\]|\\.)*)'", fallback_block.group(1)))
        if fallback_block
        else {}
    )
    for label, key in sorted(mapping.items()):
        for lang in LANGS:
            if key not in bundles[lang]:
                report.add("card", lang, label, f"{key} is not in {lang}.json")
        if label in fallback and key in en_bundle and fallback[label] != en_bundle[key]:
            report.add(
                "card", "en", label,
                f"fallback {fallback[label]!r} ≠ {key} {en_bundle[key]!r}",
            )


def check_hardcoded(report: Report, www: Path, glossary: dict) -> None:
    allow = set(glossary.get("hardcodedAllow", {}))
    for page in sorted(www.glob("*.html")):
        source = page.read_text("utf-8")
        for m in _ATTR_RE.finditer(source):
            tag = m.group("tag").lower()
            attrs = dict(_HTML_ATTR_RE.findall(m.group("attrs")))
            if attrs.get("id") in allow:
                continue
            for attr in ("aria-label", "title", "placeholder"):
                value = attrs.get(attr)
                if not value or "{{" in value or (attr == "title" and tag == "title"):
                    continue
                if f"data-i18n-{attr}" in attrs:
                    continue
                line = source[: m.start()].count("\n") + 1
                report.add(
                    "hardcoded", "", f"{page.name}:{line}",
                    f'{attr}="{value}" has no data-i18n-{attr}',
                )


def check_punctuation(
    report: Report, texts: dict[str, dict[str, str]], glossary: dict
) -> None:
    exceptions = glossary.get("punctuationExceptions", [])
    acronyms = set(glossary.get("acronyms", []))
    proper = set(glossary.get("properNouns", []))
    for lang in LANGS:
        for key, text in sorted(texts.get(lang, {}).items()):
            if _match(key, exceptions):
                continue
            if "..." in text:
                report.add("punct", lang, key, f'"..." → "…"   "{_short(text)}"')
            if re.search(r"\s…", text):
                report.add("punct", lang, key, f'space before "…"   "{_short(text)}"')
            for word in _CAPS_WORD_RE.findall(display_text(text)):
                if word not in acronyms:
                    report.add(
                        "punct", lang, key, f"capitals {word!r} → CSS text-transform"
                    )
            if lang == "en":
                _check_sentence_case(report, key, text, proper, acronyms)


def _check_sentence_case(
    report: Report, key: str, text: str, proper: set[str], acronyms: set[str]
) -> None:
    """Short English labels (no sentence punctuation, ≤ 5 words) in Title Case."""
    shown = display_text(text).strip()
    if re.search(r"[.!?]$", shown) or len(shown.split()) > 5:
        return
    for segment in re.split(r"\s[—·:|-]\s|:\s|\n", shown):
        # Leading icons and arrows ("⭐ Featured", "← Back") are not a word.
        words = [w for w in segment.split() if re.search(r"[A-Za-z]", w)]
        for word in words[1:]:
            bare = word.strip("()\"'“”‘’,…→←▶︎⭐🚩⚡+")
            if not bare or not bare[0].isupper():
                continue
            if bare in proper or bare in acronyms or bare.startswith("Name"):
                continue
            report.add("punct", "en", key, f"Title Case {bare!r}   \"{shown}\"")
            break


def check_unused(report: Report, en: dict[str, str], www: Path, pkg: Path) -> None:
    parts: list[str] = []
    for path in sorted(www.rglob("*")):
        if not path.is_file() or path.suffix not in (".html", ".js"):
            continue
        if path.name == "player.bundle.js" or path.parent.name == "i18n":
            continue
        parts.append(path.read_text("utf-8", errors="replace"))
    for path in sorted(pkg.rglob("*.py")):
        parts.append(path.read_text("utf-8", errors="replace"))
    corpus = "\n".join(parts)
    for key in sorted(en):
        if key in corpus or key.startswith(DYNAMIC_PREFIXES):
            continue
        report.add("unused", "", key, "referenced nowhere under www/ or in the Python")


def _prose(text: str) -> str:
    """The words of a template without its placeholder names, so a param
    called {round} or {packs} is not mistaken for the word."""
    text = _PLURAL_HEAD_RE.sub("{", text)
    return re.sub(r"\{\w+\}", "", text)


def _short(text: str, width: int = 48) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(root: Path, glossary_file: Path | None = None) -> Report:
    pkg = root / "custom_components" / "quizify"
    www = pkg / "www"
    i18n = www / "i18n"
    raw = {
        lang: json.loads((i18n / f"{lang}.json").read_text("utf-8")) for lang in LANGS
    }
    bundles = {lang: _leaves(raw[lang]) for lang in LANGS}
    glossary_path = glossary_file or i18n / "glossary.json"
    glossary = (
        json.loads(glossary_path.read_text("utf-8")) if glossary_path.is_file() else {}
    )
    tts = _load_tts(pkg / "game" / "tts_phrases.py")
    texts = {lang: {**bundles[lang], **tts.get(lang, {})} for lang in LANGS}

    report = Report()
    check_parity(report, {lang: texts[lang] for lang in LANGS})
    check_glossary(report, texts, glossary)
    check_length(report, bundles, glossary)
    check_fragments(report, bundles["en"])
    check_card(report, www / "cards" / "quizify-host-card.js", bundles["en"], bundles)
    check_hardcoded(report, www, glossary)
    check_punctuation(report, texts, glossary)
    check_unused(report, bundles["en"], www, pkg)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument(
        "--glossary", type=Path, default=None,
        help="a glossary.json other than the one under --root",
    )
    parser.add_argument("--strict", action="store_true", help="exit 1 on findings")
    parser.add_argument("--summary", action="store_true", help="print the summary only")
    args = parser.parse_args(argv)

    report = run(args.root, args.glossary)
    if not args.summary:
        for finding in report.findings:
            print(finding.line())
    print(report.summary())
    if args.strict and report.findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
