---
name: quizify-stats
description: Generate a Markdown stats report for the Quizify Home Assistant integration. Collects GitHub data (stars, forks, issues, PRs, releases, traffic, referrers), HACS/HA-analytics installs and rank, Reddit mentions in the Home Assistant subreddits, and Home Assistant Community forum topics — with day-over-day deltas from a rolling history file. Use this skill whenever the user asks for Quizify stats, metrics, an analytics report, community presence, install numbers, star count, or how the project is doing.
---

# Quizify Stats Report Generator

One Python script collects four sources and writes a Markdown report with deltas.

## How to use

```bash
python3 <skill-path>/scripts/generate_stats.py --output /tmp/quizify-stats.md
```

Flags:

| Flag | Default | Purpose |
|---|---|---|
| `--output` / `-o` | `/tmp/quizify-stats.md` | Report path |
| `--history` | `~/Development/Claude/quizifybot/commands/quizify-stats/quizify-stats-history.json` | Rolling snapshot file |
| `--dry-run` | off | Write the report, leave the history untouched |

No API keys. GitHub goes through the `gh` CLI, which carries its own credentials — there is no token to rotate and none that expires silently.

## Data sources

### GitHub (`gh api repos/mholzi/quizify`)
Stars, forks, watchers, open issues (PR count subtracted — `open_issues_count` includes PRs), open PRs, contributors, licence, last push. Latest **stable** release *and* latest **pre-release** separately, because Quizify ships RC tags between stable versions and a single `releases/latest` would hide what is actually in the field. Traffic views, clones and referrers where `gh` has push access.

### HACS / HA analytics (`analytics.home-assistant.io/custom_integrations.json`)
Install count, rank among all custom integrations, percentile, version distribution.

Three outcomes are kept strictly apart:

- **listed** — install figure and rank,
- **reachable but not listed** — a real measurement, reported as such. Quizify entered the HACS default list on 2026-08-01 but is not in this file yet; the file only counts instances that opted into analytics, which is a different population from "installable via HACS",
- **unreachable** — unknown, never rendered as `0`.

The first run where an install figure appears is flagged as a milestone in the report, because the snapshot stores `hacs_listed` explicitly rather than leaving it implied by a missing number.

### Reddit
`r/homeassistant` and `r/homeautomation` with `restrict_sr=on`, plus one global search.

**Scope decides relevance, not a guess.** "Quizify" also names an unrelated SaaS quiz product (r/Quizify_io), an Anki markdown tool and assorted quiz threads. Hits inside the two Home-Assistant subreddits are about this project by construction and carry the headline number; global hits are listed separately and never counted into it.

Not `r/HACS` — that subreddit does not exist (verified 2026-08-13: 404 on the same route where r/homeassistant answers 200).

Reddit blocks server-side reads, so the fetch is curl first and a **browser detour** second: a `fetch()` issued from a CDP-driven Chrome tab that itself sits on reddit.com, therefore same-origin and carrying the browser session. If both fail, the source is reported as unreachable — never as zero. Requires a Chrome with `--remote-debugging-port=9222` and the `websocket-client` package; without them the detour is skipped and the reason is named in the report.

### Home Assistant Community (`community.home-assistant.io/search.json`)
Topics mentioning "quizify" with views, replies and last activity. Currently zero, which is a measured zero.

## History and deltas

A rolling `quizify-stats-history.json` keeps the last 90 daily snapshots — key metrics plus a fingerprint per post (comment count, score, views).

- Metrics render with a delta: `(+3)`, `(-1)`, blank when unchanged.
- Posts appear only when they are **new** or have **new comments**; otherwise view growth is summarised in one line.
- A second run on the same day replaces that day's snapshot and still compares against the previous *day*, so deltas do not collapse to zero.

**The history path is a constant, not derived from `--output`.** The Beatify skill defaulted to "next to the output file" and ended up with three copies — 90 snapshots in the maintained one, 6 and 1 in two dead ones — so any run without `--history` silently compared against April data. On top of that, a comparison snapshot older than 3 days is called out in the report header instead of being used quietly.

## Failure behaviour

Every source fails on its own. A dead source is reported as `n/a` with a reason, never as a measurement, and the report still renders. That distinction exists because an outage rendered as `0 posts` reads like news.

## Report structure

```
# Quizify Stats Report
> Generated / Compared to / stale-history warning

## GitHub                            (Change column, traffic, referrers)
## HACS / HA analytics               (or "not listed yet", or "unreachable")
## Reddit                            (subreddit hits + global hits apart)
## Home Assistant Community forum
## Summary
```

## Deliberately not included

- **YouTube** — Beatify tracks it; Quizify has no video presence, and the section would need an API key to render "0".
- **simon42 forum** — no German-forum presence to track yet.
- **Content metrics** (packs, questions, difficulty spread) — that is `commands/quiz-pack-stats/command.md` in the QuizifyBot; this skill measures outside reach, not the library.

## Consumer

`~/Development/Claude/quizifybot/commands/quizify-stats/command.md` runs this daily at 13:30 and sends a short card to the PWA chat.
