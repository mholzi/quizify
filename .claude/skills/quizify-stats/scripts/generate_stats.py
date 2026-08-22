#!/usr/bin/env python3
"""Generate a Markdown stats report for the Quizify project.

Four sources: GitHub, HACS/HA analytics, Reddit, Home Assistant Community
forum. Maintains a JSON history file so every run can show deltas and only
surface posts that are new or have new comments.

Adapted from the Beatify skill of the same name. Three things differ, each for
a reason recorded at the relevant function:

* the history path defaults to ONE canonical location instead of "next to the
  output file" (Beatify ended up with three copies, two of them dead),
* Quizify is not in HA analytics yet, so "not listed" and "fetch failed" are
  kept strictly apart and the first appearance is reported as a milestone,
* the Reddit name collision (an unrelated Quizify SaaS) is handled by scope
  rather than by guessing: subreddit hits carry the headline number, global
  hits are listed separately and never counted into it.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path


GITHUB_REPO = "mholzi/quizify"
HACS_KEY = "quizify"
HACS_API = "https://analytics.home-assistant.io/custom_integrations.json"
HA_COMMUNITY = "https://community.home-assistant.io"
SEARCH_TERM = "quizify"

#: Subreddits searched with ``restrict_sr=on``. Both are Home-Assistant
#: context by construction, so a hit there is about *this* Quizify and needs
#: no name-collision filtering.
#:
#: NOT r/HACS: that subreddit does not exist. Verified 2026-08-13 — a search
#: against it answers 404 while r/homeassistant answers 200 on the same route,
#: and r/HACS as well as r/hacs are both unreachable via the browser detour.
#: The `reddit-mentions` job still names it in its config; searching a
#: non-existent subreddit costs a request and returns nothing, forever.
REDDIT_SUBREDDITS = ["homeassistant", "homeautomation"]

#: The canonical history file. Deliberately NOT derived from --output: the
#: Beatify skill defaulted to "next to the output" and accumulated three
#: copies (90 snapshots in the maintained one, 6 and 1 in two stale ones), so
#: a run without --history silently compared against April data. One constant
#: means a wrong path has to be typed on purpose.
DEFAULT_HISTORY = str(
    Path.home()
    / "Development/Claude/quizifybot/commands/quizify-stats/quizify-stats-history.json"
)

#: A history whose newest snapshot is older than this is reported as suspect
#: instead of being used silently. This is the guard the Beatify skill lacked.
STALE_HISTORY_DAYS = 3

HISTORY_KEEP_DAYS = 90


# ── History ──────────────────────────────────────────────────────────────────

def load_history(history_path):
    """Load history JSON. Returns a dict with a 'snapshots' list."""
    if os.path.exists(history_path):
        try:
            with open(history_path) as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("snapshots"), list):
                return data
            print(
                f"  Warning: {history_path} has an unexpected shape — starting fresh.",
                file=sys.stderr,
            )
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  Warning: cannot read {history_path}: {exc}", file=sys.stderr)
    return {"snapshots": []}


def save_history(history_path, history):
    """Save history JSON, creating the directory if needed."""
    Path(history_path).parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)


def get_previous_snapshot(history):
    """Most recent snapshot that is not today's, or None.

    Today's own snapshot must be skipped: the script overwrites it on a second
    run of the same day, and comparing against it would show every delta as 0.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for snap in reversed(history["snapshots"]):
        if snap.get("date") != today:
            return snap
    return None


def history_staleness(prev_snapshot):
    """Days between the newest usable snapshot and today, or None.

    Returned so the report can say "compared against something old" out loud.
    A silently stale history turns every delta into fiction.
    """
    if not prev_snapshot:
        return None
    try:
        prev = date.fromisoformat(prev_snapshot["date"])
    except (KeyError, ValueError):
        return None
    return (datetime.now(timezone.utc).date() - prev).days


# ── Delta helpers ────────────────────────────────────────────────────────────

def delta_str(current, previous, key):
    """Return a delta string like ' (+3)', ' (-1)' or ''."""
    if previous is None or current is None:
        return ""
    prev_val = previous.get("metrics", {}).get(key)
    if prev_val is None:
        return ""
    diff = current - prev_val
    if diff > 0:
        return f" (+{diff})"
    if diff < 0:
        return f" ({diff})"
    return ""


def is_new_or_updated_post(url, current_data, prev_posts):
    """'new', '+N comments' or None, comparing against the previous snapshot."""
    if url not in prev_posts:
        return "new"
    prev = prev_posts[url]

    def comments_of(d):
        for key in ("num_comments", "posts_count"):
            if key in d:
                return d[key]
        return 0

    diff = comments_of(current_data) - comments_of(prev)
    if diff > 0:
        return f"+{diff} comments"
    return None


# ── Fetch plumbing ───────────────────────────────────────────────────────────

class SourceData(list):
    """A fetch result that remembers whether its source was reachable.

    A plain empty list is ambiguous — it means both "nothing found" and "could
    not ask". Rendering the second case as `0` turns an outage into a
    measurement, which is exactly how Reddit's 403 wall got reported as "0
    posts" for a full day in the Beatify skill.
    """

    def __init__(self, items=(), unreachable=False, detail=""):
        super().__init__(items)
        self.unreachable = unreachable
        self.detail = detail


def _mark(value, unreachable=False, detail=""):
    """Attach reachability info to a dict-shaped fetch result."""
    if isinstance(value, dict):
        value["_unreachable"] = unreachable
        value["_detail"] = detail
    return value


#: Sent instead of a spoofed browser string. Both Home Assistant hosts sit
#: behind a bot filter that rejects a request *claiming* to be Chrome without a
#: browser's TLS fingerprint behind it — the old header cost every run its
#: analytics and forum numbers, while an honest tool name passes:
#:
#:     Chrome UA          analytics 403   forum 403
#:     Python-urllib/3.x  analytics 403   forum 403
#:     Quizify-Stats/1.0  analytics 200   forum 200
#:
#: Python's own default is rejected too, so this is not "any UA works" — it is
#: that pretending to be a browser is what gets caught.
USER_AGENT = "Quizify-Stats/1.0 (+https://github.com/mholzi/quizify)"


def fetch_json(url, headers=None, timeout=15):
    """Fetch JSON over urllib, falling back to curl, None if both fail."""
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        TimeoutError,
    ) as exc:
        print(f"  Warning: failed to fetch {url}: {exc}", file=sys.stderr)

    # Second opinion before declaring a source dead. urllib and curl disagree
    # often enough — different TLS stack, different header order — and the cost
    # of being wrong here is a report that says "unreachable" about a host that
    # answered 200 to everything else on the machine.
    fallback = fetch_json_curl(url, timeout=timeout)
    if fallback is not None:
        print(f"  Recovered via curl: {url}", file=sys.stderr)
    return fallback


def fetch_json_curl(url, timeout=15):
    """Fetch JSON via curl — more reliable than urllib against Reddit."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-m", str(timeout), "-H", f"User-Agent: {USER_AGENT}", url],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        print(f"  Warning: curl failed for {url}: {exc}", file=sys.stderr)
    return None


def _gh_binary():
    """Absolute path to the `gh` CLI, or the bare name as a last resort.

    launchd hands a job a minimal PATH containing neither /opt/homebrew/bin nor
    /usr/local/bin, so shutil.which("gh") returns None there. Resolving the
    binary explicitly is what makes a scheduled run work at all — the Beatify
    version called a bare "gh" and every scheduled run died with ENOENT while
    interactive testing stayed green.
    """
    found = shutil.which("gh")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh"):
        if os.path.exists(candidate):
            return candidate
    return "gh"


def fetch_gh_api(path, timeout=30):
    """GitHub API via the `gh` CLI (it carries its own, non-expiring credentials).

    Preferred over a PAT in the environment: a dead PAT in the Beatify
    LaunchAgent returned 401 for three months and stored 90 consecutive
    snapshots with `github_stars: null` without anyone noticing.
    """
    ref = f"repos/{GITHUB_REPO}{path}"
    try:
        out = subprocess.run(
            [_gh_binary(), "api", ref],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if out.returncode != 0:
            print(
                f"  Warning: gh api {ref} failed: {out.stderr.strip()[:200]}",
                file=sys.stderr,
            )
            return None
        return json.loads(out.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        print(f"  Warning: gh api {ref} failed: {exc}", file=sys.stderr)
        return None


# ── GitHub ───────────────────────────────────────────────────────────────────

def fetch_github_stats():
    """Repo counters, latest release (stable + pre-release) and traffic."""
    print("Fetching GitHub stats...")
    stats = {}

    data = fetch_gh_api("")
    if data:
        stats["stars"] = data.get("stargazers_count", 0)
        stats["forks"] = data.get("forks_count", 0)
        stats["watchers"] = data.get("subscribers_count", 0)
        # open_issues_count counts PRs too; corrected below once PRs are known.
        stats["open_issues"] = data.get("open_issues_count", 0)
        stats["updated_at"] = data.get("pushed_at", "")
        stats["license"] = (data.get("license") or {}).get("spdx_id", "N/A")

    open_prs = fetch_gh_api("/pulls?state=open&per_page=100")
    if open_prs is not None:
        stats["open_prs"] = len(open_prs)
        stats["open_issues"] = max(0, stats.get("open_issues", 0) - stats["open_prs"])

    contributors = fetch_gh_api("/contributors?per_page=100")
    if contributors:
        stats["contributors"] = len(contributors)

    # Quizify ships pre-releases between stable tags (RC scheme), so a single
    # /releases/latest would hide the RC that is actually out in the field.
    releases = fetch_gh_api("/releases?per_page=20")
    if releases:
        stable = next((r for r in releases if not r.get("prerelease")), None)
        pre = next((r for r in releases if r.get("prerelease")), None)
        if stable:
            stats["latest_release"] = {
                "tag": stable.get("tag_name", ""),
                "date": stable.get("published_at", ""),
            }
        if pre:
            stats["latest_prerelease"] = {
                "tag": pre.get("tag_name", ""),
                "date": pre.get("published_at", ""),
            }

    views = fetch_gh_api("/traffic/views")
    if views:
        stats["traffic_views"] = {
            "total": views.get("count", 0),
            "unique": views.get("uniques", 0),
        }

    clones = fetch_gh_api("/traffic/clones")
    if clones:
        stats["traffic_clones"] = {
            "total": clones.get("count", 0),
            "unique": clones.get("uniques", 0),
        }

    referrers = fetch_gh_api("/traffic/popular/referrers")
    if referrers:
        stats["referrers"] = [
            {"source": r["referrer"], "count": r["count"], "unique": r["uniques"]}
            for r in referrers[:10]
        ]

    return stats


# ── HACS / HA analytics ──────────────────────────────────────────────────────

def fetch_hacs_stats():
    """Install count and rank from HA analytics.

    Quizify entered the HACS default list on 2026-08-01 but has not appeared in
    `custom_integrations.json` yet: that file counts instances that opted into
    analytics, which is a different population from "listed in HACS". So three
    outcomes are kept apart — reachable-and-listed, reachable-but-not-listed,
    and unreachable. Collapsing the last two into "0 installs" would invent a
    measurement.
    """
    print("Fetching HACS / HA analytics stats...")
    data = fetch_json(HACS_API, timeout=30)
    if not data:
        return None  # unreachable

    entry = data.get(HACS_KEY)
    if not entry:
        return {"found": False, "total_integrations": len(data)}

    ranked = sorted(data.items(), key=lambda kv: kv[1].get("total", 0), reverse=True)
    rank = next((i + 1 for i, (name, _) in enumerate(ranked) if name == HACS_KEY), None)

    return {
        "found": True,
        "total_installs": entry.get("total", 0),
        "versions": entry.get("versions", {}),
        "rank": rank,
        "total_integrations": len(data),
    }


# ── Reddit, incl. the browser detour ─────────────────────────────────────────
# Reddit blocks server-side reads: the MCP answers "HTTP error (0)" and a
# direct search.json call gets 403. What works is a fetch issued from a browser
# tab that is ITSELF on reddit.com — there the request carries the browser's
# session and is same-origin. Three traps, all of them load-bearing:
#   1. the CDP WebSocket handshake is rejected with 403 while an Origin header
#      is present -> suppress_origin=True,
#   2. fetch() from an arbitrary tab returns status 0 because of CORS, so the
#      tab must be on reddit.com,
#   3. Chrome requires PUT on /json/new; a GET answers 405.
# The detour is a FALLBACK, not a replacement: curl first, browser second, and
# if both fail the result stays unreachable=True. An outage is reported as an
# outage, never as 0. This mirrors the CLAUDE.md rule for blocked sources.
_CDP_STATE = {"checked": False, "available": False, "reason": ""}


def _cdp_available():
    """Check once whether a CDP Chrome listens on 9222 and websocket-client is there."""
    if _CDP_STATE["checked"]:
        return _CDP_STATE["available"]
    _CDP_STATE["checked"] = True
    try:
        import websocket  # noqa: F401
    except ImportError:
        _CDP_STATE["reason"] = "websocket-client not installed"
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=5):
            pass
    except Exception as exc:  # noqa: BLE001 — any failure means "no browser"
        _CDP_STATE["reason"] = f"no CDP Chrome on 9222 ({exc.__class__.__name__})"
        return False
    _CDP_STATE["available"] = True
    return True


def fetch_json_cdp(url, timeout=25):
    """Fetch Reddit JSON through a browser tab that sits on reddit.com itself."""
    if not _cdp_available():
        return None
    import websocket

    tab_id = None
    ws = None
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:9222/json/new?"
            + urllib.parse.quote("https://www.reddit.com/", safe=":/"),
            method="PUT",
        )
        tab = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        tab_id = tab["id"]
        ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], suppress_origin=True, timeout=timeout + 10
        )
        time.sleep(4)  # the tab must have loaded, or the origin is still about:blank
        expr = (
            "fetch(%r).then(async r=>JSON.stringify({s:r.status,b:(await r.text())}))"
            ".catch(e=>JSON.stringify({s:0,e:String(e)}))" % url
        )
        ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expr,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
        )
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                break
        payload = json.loads(msg.get("result", {}).get("result", {}).get("value"))
        if payload.get("s") != 200:
            print(
                f"  Warning: CDP fallback for {url}: HTTP {payload.get('s')}",
                file=sys.stderr,
            )
            return None
        return json.loads(payload["b"])
    except Exception as exc:  # noqa: BLE001 — the detour must never raise
        print(
            f"  Warning: CDP fallback failed ({exc.__class__.__name__}: {exc})",
            file=sys.stderr,
        )
        return None
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        if tab_id:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(f"http://127.0.0.1:9222/json/close/{tab_id}"),
                    timeout=10,
                ).read()
            except Exception:  # noqa: BLE001
                pass


def _reddit_fetch(url):
    """curl first, browser detour second. Returns (data, route)."""
    data = fetch_json_curl(url)
    if data and "data" in data:
        return data, "curl"
    data = fetch_json_cdp(url)
    if data and "data" in data:
        return data, "cdp"
    return None, "failed"


def _reddit_items(data, scope, fallback_sub):
    """Flatten a Reddit listing into our post shape."""
    posts = []
    for child in data["data"].get("children", []):
        post = child.get("data", {})
        posts.append(
            {
                "scope": scope,
                "subreddit": post.get("subreddit", fallback_sub),
                "title": post.get("title", ""),
                "url": f"https://reddit.com{post.get('permalink', '')}",
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "created": datetime.fromtimestamp(
                    post.get("created_utc", 0), tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                "author": post.get("author", ""),
            }
        )
    return posts


def fetch_reddit_posts():
    """Subreddit hits (headline) plus global hits (separate, name collisions).

    "quizify" also names an unrelated SaaS quiz product, so a global search
    returns strangers: r/Quizify_io, an Anki markdown tool, unrelated quiz
    threads. Rather than guessing which hit is ours, scope decides — hits
    inside r/homeassistant and r/HACS are ours by construction and carry the
    headline number; global hits are listed apart and never counted into it.
    """
    print("Fetching Reddit posts...")
    posts = []
    failed = 0
    used_cdp = 0
    targets = []

    for sub in REDDIT_SUBREDDITS:
        targets.append(
            (
                "sub",
                sub,
                f"https://www.reddit.com/r/{sub}/search.json"
                f"?q={SEARCH_TERM}&restrict_sr=on&sort=new&limit=25",
            )
        )
    targets.append(
        (
            "global",
            "",
            f"https://www.reddit.com/search.json?q={SEARCH_TERM}&sort=new&limit=25",
        )
    )

    for scope, sub, url in targets:
        data, route = _reddit_fetch(url)
        time.sleep(2)
        if route == "failed":
            failed += 1
            continue
        if route == "cdp":
            used_cdp += 1
        posts.extend(_reddit_items(data, scope, sub))

    # A post can show up in both a subreddit search and the global one; the
    # subreddit scope wins so it stays in the headline count.
    deduped = {}
    for post in posts:
        existing = deduped.get(post["url"])
        if existing is None or (existing["scope"] == "global" and post["scope"] == "sub"):
            deduped[post["url"]] = post
    posts = list(deduped.values())

    total = len(targets)
    if failed == total:
        why = f"all {total} Reddit searches unreachable"
        if not _CDP_STATE["available"] and _CDP_STATE["reason"]:
            why += f" (browser detour unavailable too: {_CDP_STATE['reason']})"
        else:
            why += " (curl AND browser detour failed)"
        return SourceData([], unreachable=True, detail=why)

    notes = []
    if used_cdp:
        notes.append(f"{used_cdp} of {total} searches only reachable via the browser detour")
    if failed:
        notes.append(f"{failed} of {total} searches unreachable — count incomplete")
    return SourceData(posts, detail="; ".join(notes))


# ── Discourse ────────────────────────────────────────────────────────────────

def fetch_discourse_posts(base_url, forum_name):
    """Search a Discourse forum for the term."""
    print(f"Fetching {forum_name} posts...")
    url = f"{base_url}/search.json?q=%22{SEARCH_TERM}%22"
    data = fetch_json(url)
    if not data:
        return _mark(
            {"topics": [], "posts": []},
            unreachable=True,
            detail=f"{forum_name} unreachable",
        )

    topics = [
        {
            "title": t.get("title", ""),
            "url": f"{base_url}/t/{t.get('slug', '')}/{t.get('id', '')}",
            "posts_count": t.get("posts_count", 0),
            "reply_count": t.get("reply_count", 0),
            # Discourse's search payload carries no view count — only the
            # topic endpoint does. Defaulting to 0 printed "0 views" for a
            # topic that had 26, which is the same mistake as reporting an
            # unreachable source as empty.
            "views": t.get("views"),
            "created_at": (t.get("created_at") or "")[:10],
            "last_posted_at": (t.get("last_posted_at") or "")[:10],
        }
        for t in data.get("topics", [])
    ]
    posts = [
        {
            "topic_id": p.get("topic_id"),
            "username": p.get("username", ""),
            "created_at": (p.get("created_at") or "")[:10],
            "blurb": p.get("blurb", ""),
        }
        for p in data.get("posts", [])
    ]
    return _mark({"topics": topics, "posts": posts})


# ── Snapshot ─────────────────────────────────────────────────────────────────

def build_snapshot(github, hacs, reddit, ha_forum):
    """Key metrics plus post fingerprints for the next run's comparison."""
    snapshot = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "metrics": {},
        "posts": {},
    }
    metrics = snapshot["metrics"]

    if github:
        for key, field in (
            ("github_stars", "stars"),
            ("github_forks", "forks"),
            ("github_watchers", "watchers"),
            ("github_open_issues", "open_issues"),
            ("github_open_prs", "open_prs"),
            ("github_contributors", "contributors"),
        ):
            if field in github:
                metrics[key] = github[field]
        if "traffic_views" in github:
            metrics["github_views_total"] = github["traffic_views"]["total"]
            metrics["github_views_unique"] = github["traffic_views"]["unique"]
        if "traffic_clones" in github:
            metrics["github_clones_total"] = github["traffic_clones"]["total"]
            metrics["github_clones_unique"] = github["traffic_clones"]["unique"]

    # hacs_listed is stored explicitly so the day Quizify first appears in HA
    # analytics is detectable as an event, not just as "a number where None was".
    if hacs is None:
        metrics["hacs_listed"] = None  # unreachable — unknown, not absent
    elif hacs.get("found"):
        metrics["hacs_listed"] = True
        metrics["hacs_installs"] = hacs["total_installs"]
        metrics["hacs_rank"] = hacs["rank"]
    else:
        metrics["hacs_listed"] = False

    if not getattr(reddit, "unreachable", False):
        snapshot["posts"]["reddit"] = {
            p["url"]: {
                "title": p["title"],
                "num_comments": p["num_comments"],
                "score": p["score"],
                "scope": p["scope"],
            }
            for p in (reddit or [])
        }
    if not (ha_forum or {}).get("_unreachable"):
        snapshot["posts"]["ha_forum"] = {
            t["url"]: {
                "title": t["title"],
                "posts_count": t["posts_count"],
                "reply_count": t["reply_count"],
                "views": t["views"],
            }
            for t in (ha_forum or {}).get("topics", [])
        }
    return snapshot


# ── Report ───────────────────────────────────────────────────────────────────

def format_date(iso_str):
    """First 10 chars of an ISO timestamp, or 'N/A'."""
    if not iso_str:
        return "N/A"
    try:
        return iso_str[:10]
    except (IndexError, TypeError):
        return "N/A"


def _age_days(iso_str):
    """Whole days since an ISO timestamp, or None."""
    try:
        then = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - then).days


def _format_view_deltas(topics, prev_topics):
    """Comma-joined view growth for forum topics, or None."""
    parts = []
    for topic in topics:
        prev_views = prev_topics.get(topic["url"], {}).get("views")
        views = topic.get("views")
        if views is None or prev_views is None:
            continue
        if views > prev_views:
            title = topic["title"][:30] + ("..." if len(topic["title"]) > 30 else "")
            parts.append(f"{title} ({prev_views}→{views}, +{views - prev_views})")
    return ", ".join(parts) if parts else None


def generate_report(github, hacs, reddit, ha_forum, prev_snapshot, stale_days):
    """The full Markdown report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# Quizify Stats Report", f"> Generated: {now}"]
    if prev_snapshot:
        lines.append(f"> Compared to: {prev_snapshot['date']}")
    else:
        lines.append("> No previous snapshot — first run, deltas are blank.")
    if stale_days is not None and stale_days > STALE_HISTORY_DAYS:
        lines.append(
            f"> ⚠️ The comparison snapshot is **{stale_days} days old**. Either the job "
            f"did not run, or a wrong `--history` path was used — treat every delta "
            f"below as covering {stale_days} days, not one."
        )
    lines.append("")

    # ── GitHub ──
    lines += ["---", "## GitHub", ""]
    if github:
        lines.append(f"**Repository**: [{GITHUB_REPO}](https://github.com/{GITHUB_REPO})")
        lines.append("")
        lines += ["| Metric | Value | Change |", "|---|---|---|"]
        for label, field, key in (
            ("Stars", "stars", "github_stars"),
            ("Forks", "forks", "github_forks"),
            ("Watchers", "watchers", "github_watchers"),
            ("Open Issues", "open_issues", "github_open_issues"),
            ("Open PRs", "open_prs", "github_open_prs"),
            ("Contributors", "contributors", "github_contributors"),
        ):
            value = github.get(field)
            if value is None:
                lines.append(f"| {label} | n/a | |")
                continue
            lines.append(f"| {label} | {value} | {delta_str(value, prev_snapshot, key)} |")
        lines.append(f"| License | {github.get('license', 'N/A')} | |")
        lines.append(f"| Last Push | {format_date(github.get('updated_at'))} | |")
        lines.append("")

        rel = github.get("latest_release")
        if rel:
            age = _age_days(rel["date"])
            age_txt = f", {age} days ago" if age is not None else ""
            lines.append(f"**Latest release**: {rel['tag']} ({format_date(rel['date'])}{age_txt})")
        pre = github.get("latest_prerelease")
        if pre:
            age = _age_days(pre["date"])
            age_txt = f", {age} days ago" if age is not None else ""
            lines.append(
                f"**Latest pre-release**: {pre['tag']} ({format_date(pre['date'])}{age_txt})"
            )
        if rel or pre:
            lines.append("")

        if "traffic_views" in github:
            tv = github["traffic_views"]
            tc = github.get("traffic_clones", {})
            lines += [
                "### Traffic (last 14 days)",
                "",
                "| Metric | Total | Unique | Change (Total) | Change (Unique) |",
                "|---|---|---|---|---|",
                f"| Views | {tv['total']} | {tv['unique']} "
                f"| {delta_str(tv['total'], prev_snapshot, 'github_views_total')} "
                f"| {delta_str(tv['unique'], prev_snapshot, 'github_views_unique')} |",
                f"| Clones | {tc.get('total', 'N/A')} | {tc.get('unique', 'N/A')} "
                f"| {delta_str(tc.get('total'), prev_snapshot, 'github_clones_total')} "
                f"| {delta_str(tc.get('unique'), prev_snapshot, 'github_clones_unique')} |",
                "",
            ]

        if github.get("referrers"):
            lines += ["### Top referrers", "", "| Source | Views | Unique |", "|---|---|---|"]
            for ref in github["referrers"]:
                lines.append(f"| {ref['source']} | {ref['count']} | {ref['unique']} |")
            lines.append("")
    else:
        lines += ["*Failed to fetch GitHub data — this is not a zero.*", ""]

    # ── HACS ──
    lines += ["---", "## HACS / HA analytics", ""]
    prev_listed = (prev_snapshot or {}).get("metrics", {}).get("hacs_listed")
    if hacs is None:
        lines += [
            "*Failed to fetch HA analytics — unknown, not zero. The next successful "
            "run picks this up.*",
            "",
        ]
    elif hacs.get("found"):
        if prev_listed is False:
            lines += [
                "🎉 **First appearance in HA analytics.** Quizify was in the HACS default "
                "list but not yet counted here; from today there is an install figure.",
                "",
            ]
        d_rank = ""
        prev_rank = (prev_snapshot or {}).get("metrics", {}).get("hacs_rank")
        if prev_rank is not None and hacs["rank"] is not None:
            diff = prev_rank - hacs["rank"]  # positive = moved up
            if diff:
                d_rank = f" ({'+' if diff > 0 else ''}{diff} places)"
        lines += [
            "| Metric | Value | Change |",
            "|---|---|---|",
            f"| Active installs | **{hacs['total_installs']}** "
            f"| {delta_str(hacs['total_installs'], prev_snapshot, 'hacs_installs')} |",
            f"| Rank | #{hacs['rank']} of {hacs['total_integrations']} | {d_rank} |",
        ]
        if hacs["rank"]:
            pct = round(hacs["rank"] / hacs["total_integrations"] * 100, 1)
            lines.append(f"| Percentile | Top {pct}% | |")
        lines.append("")
        if hacs.get("versions"):
            lines += ["### Version distribution", "", "| Version | Installs |", "|---|---|"]
            for version, count in sorted(
                hacs["versions"].items(), key=lambda kv: kv[1], reverse=True
            ):
                lines.append(f"| {version} | {count} |")
            lines.append("")
    else:
        lines += [
            f"*Not in HA analytics yet* — {hacs.get('total_integrations', 0)} custom "
            "integrations are listed, `quizify` is not among them. Being in the HACS "
            "default list (since 2026-08-01) is a different thing: this file only counts "
            "instances that opted into analytics. **This is a real measurement, not a "
            "failed fetch.**",
            "",
        ]

    prev_posts = (prev_snapshot or {}).get("posts", {})

    # ── Reddit ──
    lines += ["---", "## Reddit", ""]
    if getattr(reddit, "unreachable", False):
        lines += [
            f"**Posts**: n/a — {reddit.detail}",
            "",
            "*The fetch failed. This is NOT a measurement — whether there are new "
            "mentions is unknown. The next successful run covers the gap.*",
            "",
        ]
    else:
        if getattr(reddit, "detail", ""):
            lines += [f"*Partial outage: {reddit.detail}*", ""]
        sub_posts = [p for p in reddit if p["scope"] == "sub"]
        global_posts = [p for p in reddit if p["scope"] == "global"]
        prev_reddit = prev_posts.get("reddit", {})

        lines.append(
            f"**Posts in r/{' + r/'.join(REDDIT_SUBREDDITS)}**: {len(sub_posts)}"
        )
        updated = [
            (p, is_new_or_updated_post(p["url"], {"num_comments": p["num_comments"]}, prev_reddit))
            for p in sub_posts
        ]
        updated = [(p, s) for p, s in updated if s or not prev_snapshot]
        if prev_snapshot and updated:
            new_count = sum(1 for _, s in updated if s == "new")
            comment_count = sum(1 for _, s in updated if s and s != "new")
            parts = []
            if new_count:
                parts.append(f"{new_count} new")
            if comment_count:
                parts.append(f"{comment_count} with new comments")
            if parts:
                lines.append(f"**Updates**: {', '.join(parts)}")
        lines.append("")

        if updated:
            lines += [
                "| Status | Date | Subreddit | Title | Score | Comments |",
                "|---|---|---|---|---|---|",
            ]
            for post, status in updated:
                badge = "NEW" if status == "new" else (status or "")
                title = post["title"][:55] + ("..." if len(post["title"]) > 55 else "")
                lines.append(
                    f"| {badge} | {post['created']} | r/{post['subreddit']} | "
                    f"[{title}]({post['url']}) | {post['score']} | {post['num_comments']} |"
                )
            lines.append("")
        elif prev_snapshot:
            lines += ["*No new posts or comments since the last report.*", ""]

        if global_posts:
            lines += [
                f"### Global search hits ({len(global_posts)}, not counted above)",
                "",
                "*A global search for \"quizify\" also finds an unrelated SaaS quiz "
                "product and other name collisions. Listed for completeness; only the "
                "subreddit hits above carry the headline number.*",
                "",
                "| Date | Subreddit | Title | Score | Comments |",
                "|---|---|---|---|---|",
            ]
            for post in global_posts:
                title = post["title"][:55] + ("..." if len(post["title"]) > 55 else "")
                lines.append(
                    f"| {post['created']} | r/{post['subreddit']} | "
                    f"[{title}]({post['url']}) | {post['score']} | {post['num_comments']} |"
                )
            lines.append("")

    # ── HA forum ──
    lines += ["---", "## Home Assistant Community forum", ""]
    if (ha_forum or {}).get("_unreachable"):
        lines += [
            f"**Topics**: n/a — {(ha_forum or {}).get('_detail', 'unreachable')}",
            "",
            "*The fetch failed. Not a measurement.*",
            "",
        ]
    else:
        ha_topics = (ha_forum or {}).get("topics", [])
        if ha_topics:
            prev_ha = prev_posts.get("ha_forum", {})
            updated = [
                (t, is_new_or_updated_post(t["url"], {"posts_count": t["posts_count"]}, prev_ha))
                for t in ha_topics
            ]
            updated = [(t, s) for t, s in updated if s or not prev_snapshot]
            lines += [f"**Topics**: {len(ha_topics)}", ""]
            if updated:
                lines += [
                    "| Status | Topic | Views | Replies | Last activity |",
                    "|---|---|---|---|---|",
                ]
                for topic, status in updated:
                    badge = "NEW" if status == "new" else (status or "")
                    title = topic["title"][:55] + ("..." if len(topic["title"]) > 55 else "")
                    lines.append(
                        f"| {badge} | [{title}]({topic['url']}) | "
                        f"{topic['views'] if topic.get('views') is not None else '—'} | "
                        f"{topic['reply_count']} | {topic['last_posted_at']} |"
                    )
                lines.append("")
            elif prev_snapshot:
                changes = _format_view_deltas(ha_topics, prev_posts.get("ha_forum", {}))
                lines.append(
                    f"*No new comments.* View changes: {changes}"
                    if changes
                    else "*No new topics or comments since the last report.*"
                )
                lines.append("")
        else:
            lines += [
                f'*No mentions of "{SEARCH_TERM}" on the Home Assistant Community forum.*',
                "",
            ]

    # ── Summary ──
    lines += ["---", "## Summary", "", "| Platform | Key metric | Change |", "|---|---|---|"]
    if github:
        lines.append(
            f"| GitHub | {github.get('stars', 'n/a')} stars, {github.get('forks', 'n/a')} forks "
            f"| {delta_str(github.get('stars'), prev_snapshot, 'github_stars')} |"
        )
    else:
        lines.append("| GitHub | n/a — fetch failed | |")
    if hacs is None:
        lines.append("| HACS | n/a — fetch failed | |")
    elif hacs.get("found"):
        lines.append(
            f"| HACS | {hacs['total_installs']} installs (#{hacs['rank']}) "
            f"| {delta_str(hacs['total_installs'], prev_snapshot, 'hacs_installs')} |"
        )
    else:
        lines.append("| HACS | not in HA analytics yet | |")
    if getattr(reddit, "unreachable", False):
        lines.append(f"| Reddit | n/a — {reddit.detail} | |")
    else:
        caveat = f" ⚠️ {reddit.detail}" if getattr(reddit, "detail", "") else ""
        sub_count = len([p for p in reddit if p["scope"] == "sub"])
        plural = "" if sub_count == 1 else "s"
        lines.append(f"| Reddit | {sub_count} post{plural} in HA subreddits{caveat} | |")
    if (ha_forum or {}).get("_unreachable"):
        lines.append(f"| HA Forum | n/a — {(ha_forum or {}).get('_detail')} | |")
    else:
        lines.append(f"| HA Forum | {len((ha_forum or {}).get('topics', []))} topics | |")
    lines.append("")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate a Quizify stats report")
    parser.add_argument("--output", "-o", default="/tmp/quizify-stats.md", help="Output file path")
    parser.add_argument(
        "--history",
        default=DEFAULT_HISTORY,
        help=f"History JSON path (default: {DEFAULT_HISTORY})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the report but do not touch the history file",
    )
    args = parser.parse_args()

    history = load_history(args.history)
    prev_snapshot = get_previous_snapshot(history)
    stale_days = history_staleness(prev_snapshot)
    if prev_snapshot:
        print(f"Previous snapshot: {prev_snapshot['date']} ({len(history['snapshots'])} kept)")
        if stale_days is not None and stale_days > STALE_HISTORY_DAYS:
            print(
                f"  Warning: that snapshot is {stale_days} days old — wrong --history "
                f"path, or the job has not run.",
                file=sys.stderr,
            )
    else:
        print("No previous snapshot found — first run.")
    print()

    github = fetch_github_stats()
    hacs = fetch_hacs_stats()
    reddit = fetch_reddit_posts()
    ha_forum = fetch_discourse_posts(HA_COMMUNITY, "Home Assistant Community")

    snapshot = build_snapshot(github, hacs, reddit, ha_forum)

    if args.dry_run:
        print("\nDry run — history untouched.")
    else:
        if history["snapshots"] and history["snapshots"][-1]["date"] == snapshot["date"]:
            history["snapshots"][-1] = snapshot
        else:
            history["snapshots"].append(snapshot)
        if len(history["snapshots"]) > HISTORY_KEEP_DAYS:
            history["snapshots"] = history["snapshots"][-HISTORY_KEEP_DAYS:]
        save_history(args.history, history)
        print(f"\nSnapshot saved to: {args.history}")

    print("Generating report...")
    report = generate_report(github, hacs, reddit, ha_forum, prev_snapshot, stale_days)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        f.write(report)
    print(f"Report saved to: {args.output}")


if __name__ == "__main__":
    main()
