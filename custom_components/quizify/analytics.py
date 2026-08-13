"""Analytics data collection and storage for Quizify.

Provides persistent storage for game metrics,
enabling historical analysis through the analytics dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from .runtime import Runtime

_LOGGER = logging.getLogger(__name__)

MAX_DETAILED_RECORDS = 1000
RETENTION_DAYS = 90
PRUNE_INTERVAL = 10


class GameRecord(TypedDict):
    """Game record schema."""

    game_id: str
    started_at: int
    ended_at: int
    duration_seconds: int
    player_count: int
    category: str
    question_count: int
    rounds_played: int
    average_score: float
    difficulty: str
    player_scores: dict[str, int]
    winner: str


class PlayerAllTimeRecord(TypedDict):
    """Aggregated stats for one player across every game they played."""

    name: str
    games_played: int
    total_score: int
    wins: int  # games where this player was the leader
    best_streak: int
    streak_milestones_hit: int
    last_played: int  # unix seconds


class PlayerStanding(TypedDict):
    """One player's own all-time placing, as shown in the lobby (#371)."""

    rank: int
    total_players: int
    wins: int
    games_played: int
    total_score: int


class AnalyticsData(TypedDict):
    """Complete analytics data schema."""

    version: int
    games: list[GameRecord]
    # Rolled up at record_game time. Kept inside the same JSON file so a
    # single load gives the dashboard everything; pruning never touches
    # this map so all-time means all-time.
    all_time_players: dict[str, PlayerAllTimeRecord]


class QuizifyAnalytics:
    """Analytics storage with async file I/O and atomic writes."""

    def __init__(self, runtime: Runtime) -> None:
        """Initialize analytics storage."""
        self._runtime = runtime
        self._path = runtime.data_dir / "analytics.json"
        self._data: AnalyticsData = self._empty_data()
        self._games_since_prune = 0
        self._save_lock = asyncio.Lock()
        # #418: memoize compute_metrics per period. The game history only
        # changes at game end (add_game) or on prune, so recomputing on every
        # GET /analytics/data + every even-day featured-pack request wastes
        # full O(n) passes over up to 1000 records. Cache value is
        # ``(fingerprint, result)`` where fingerprint = (total_games,
        # last ended_at); a mismatch (append or prune) recomputes.
        self._metrics_cache: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}

    def _empty_data(self) -> AnalyticsData:
        """Return empty analytics data structure."""
        return {"version": 2, "games": [], "all_time_players": {}}

    def _migrate(self, data: dict[str, Any]) -> AnalyticsData:
        """Bring an on-disk record up to the current schema.

        v1 → v2 added ``all_time_players``. Rebuild it from the existing
        game history so users who upgrade get instant all-time stats
        without losing their record.
        """
        data.setdefault("games", [])
        if "all_time_players" not in data:
            all_time: dict[str, PlayerAllTimeRecord] = {}
            for g in data["games"]:
                scores = g.get("player_scores") or {}
                winner = g.get("winner") or ""
                ended_at = int(g.get("ended_at", 0))
                for name, score in scores.items():
                    rec = all_time.get(name)
                    if rec is None:
                        rec = {
                            "name": name,
                            "games_played": 0,
                            "total_score": 0,
                            "wins": 0,
                            "best_streak": 0,
                            "streak_milestones_hit": 0,
                            "last_played": 0,
                        }
                        all_time[name] = rec
                    rec["games_played"] += 1
                    rec["total_score"] += int(score)
                    if name == winner:
                        rec["wins"] += 1
                    rec["last_played"] = max(rec["last_played"], ended_at)
            data["all_time_players"] = all_time
        data["version"] = 2
        return data  # type: ignore[return-value]

    async def load(self) -> None:
        """Load analytics data from file."""
        try:
            if self._path.exists():
                content = await self._runtime.run_in_executor(self._path.read_text)
                raw = json.loads(content)
                self._data = self._migrate(raw)
                _LOGGER.debug(
                    "Loaded analytics: %d games, %d all-time players",
                    len(self._data.get("games", [])),
                    len(self._data.get("all_time_players", {})),
                )
                await self._prune_old_records()
            else:
                self._data = self._empty_data()
        except (json.JSONDecodeError, KeyError, TypeError) as err:
            _LOGGER.warning("Analytics file corrupted, recreating: %s", err)
            self._data = self._empty_data()
            await self._save()

    async def _save(self) -> None:
        """Persist analytics data with atomic write."""
        async with self._save_lock:
            try:
                def _mkdir() -> None:
                    self._path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)

                await self._runtime.run_in_executor(_mkdir)
                temp_path = self._path.with_suffix(".tmp")
                # Serialize *inside* the executor closure (#304): a full game's
                # analytics is ~0.5–1 MB and ``json.dumps`` previously ran on
                # the event loop, blocking it for the whole serialize while only
                # the write was offloaded. Snapshot the dict reference so the
                # executor sees a consistent object; drop ``indent=2`` (the file
                # is machine-read, never hand-edited) to roughly halve both the
                # payload size and the serialize cost.
                data = self._data

                def _write_atomic() -> None:
                    temp_path.write_text(json.dumps(data))
                    os.replace(temp_path, self._path)

                await self._runtime.run_in_executor(_write_atomic)
            except OSError as err:
                _LOGGER.error("Failed to save analytics: %s", err)

    def schedule_save(self) -> None:
        """Schedule non-blocking save."""
        task = asyncio.create_task(self._save())
        task.add_done_callback(self._handle_save_error)

    def _handle_save_error(self, task: asyncio.Task) -> None:
        """Log exceptions from fire-and-forget save tasks."""
        if (exc := task.exception()) is not None:
            _LOGGER.error("Unhandled error in analytics save task: %s", exc)

    async def record_game(
        self,
        game_id: str,
        category: str | None,
        difficulty: str,
        num_rounds: int,
        players: dict[str, int],
        duration_seconds: int,
        started_at: int | None = None,
        player_details: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Record a completed game.

        ``player_details`` is optional and carries per-player stats that
        aren't in the score-only dict: ``best_streak``, ``streak_milestones_hit``.
        When omitted the rollup uses score only.
        """
        now = int(time.time())
        player_count = len(players)
        avg_score = sum(players.values()) / player_count if player_count > 0 else 0
        winner = max(players, key=players.get) if players else ""  # type: ignore[arg-type]

        record: GameRecord = {
            "game_id": game_id,
            "started_at": started_at or (now - duration_seconds),
            "ended_at": now,
            "duration_seconds": duration_seconds,
            "player_count": player_count,
            "category": category or "mixed",
            "question_count": num_rounds,
            "rounds_played": num_rounds,
            "average_score": round(avg_score, 1),
            "difficulty": difficulty,
            "player_scores": players,
            "winner": winner,
        }
        await self.add_game(record)
        self._roll_into_all_time(
            players=players,
            winner=winner,
            ended_at=now,
            player_details=player_details or {},
        )
        _LOGGER.info(
            "Game %s recorded: %d players, %d rounds, winner=%s",
            game_id,
            player_count,
            num_rounds,
            winner,
        )

    def _roll_into_all_time(
        self,
        players: dict[str, int],
        winner: str,
        ended_at: int,
        player_details: dict[str, dict[str, Any]],
    ) -> None:
        """Merge this game's per-player numbers into the all-time map."""
        all_time = self._data.setdefault("all_time_players", {})
        for name, score in players.items():
            rec = all_time.get(name)
            if rec is None:
                rec = {
                    "name": name,
                    "games_played": 0,
                    "total_score": 0,
                    "wins": 0,
                    "best_streak": 0,
                    "streak_milestones_hit": 0,
                    "last_played": 0,
                }
                all_time[name] = rec
            rec["games_played"] += 1
            rec["total_score"] += int(score)
            if name == winner:
                rec["wins"] += 1
            rec["last_played"] = max(rec["last_played"], ended_at)
            details = player_details.get(name) or {}
            game_best = int(details.get("best_streak", 0) or 0)
            if game_best > rec["best_streak"]:
                rec["best_streak"] = game_best
            rec["streak_milestones_hit"] += int(
                details.get("streak_milestones_hit", 0) or 0
            )

    def get_all_time_leaderboard(
        self, limit: int | None = 25
    ) -> list[PlayerAllTimeRecord]:
        """Sorted all-time leaderboard. By total score, ties broken by wins."""
        records = list(self._data.get("all_time_players", {}).values())
        records.sort(key=lambda r: (r["total_score"], r["wins"]), reverse=True)
        return records[:limit] if limit else records

    def get_player_standing(self, name: str) -> PlayerStanding | None:
        """One player's own all-time standing, for the lobby line (#371).

        Deliberately NOT ``get_all_time_leaderboard()`` order. That list is
        sorted by total score (wins only break ties), which rewards playing
        a lot rather than winning — fine for the analytics dashboard, wrong
        for a line that is supposed to start a rivalry. Here the rank is by
        **wins**, with total score as the tiebreak. The dashboard ordering is
        untouched, so the two surfaces can disagree; the lobby line always
        says what it counts ("N wins from M games") so the number is legible
        on its own.

        Competition ranking: players with identical (wins, score) share a
        rank, same posture as the in-game leaderboard — a standing must not
        invent a placing out of dict order.

        Returns ``None`` when this name has never finished a game, so a
        first-time guest gets no line at all rather than "1st of 1".
        """
        records = list(self._data.get("all_time_players", {}).values())
        if not records:
            return None

        records.sort(key=lambda r: (r["wins"], r["total_score"]), reverse=True)

        rank = 0
        prev: tuple[int, int] | None = None
        for i, rec in enumerate(records):
            key = (rec["wins"], rec["total_score"])
            if prev is None or key != prev:
                rank = i + 1
                prev = key
            if rec["name"] == name:
                return {
                    "rank": rank,
                    "total_players": len(records),
                    "wins": rec["wins"],
                    "games_played": rec["games_played"],
                    "total_score": rec["total_score"],
                }
        return None

    async def add_game(self, record: GameRecord) -> None:
        """Add game record and schedule save."""
        self._data["games"].append(record)
        self._games_since_prune += 1
        # #418: new game → drop the memoized metrics so the next request
        # recomputes. The fingerprint check in compute_metrics would already
        # catch this, but clearing here keeps the cache from carrying stale
        # per-period entries and bounds its size.
        self._metrics_cache.clear()

        if self._games_since_prune >= PRUNE_INTERVAL:
            await self._prune_old_records()
            self._games_since_prune = 0

        self.schedule_save()

        _LOGGER.info(
            "Recorded analytics for game %s: %d players, %d rounds",
            record["game_id"],
            record["player_count"],
            record["rounds_played"],
        )

    async def _prune_old_records(self) -> None:
        """Prune old records past retention period, always applying age filter first."""
        now = time.time()
        cutoff = now - (RETENTION_DAYS * 24 * 60 * 60)

        games = self._data["games"]
        original_count = len(games)

        # Always apply age-based filter first (regardless of total count)
        games = [g for g in games if g["ended_at"] >= cutoff]

        # Then cap at max record count to prevent unbounded growth
        if len(games) > MAX_DETAILED_RECORDS:
            games = games[-MAX_DETAILED_RECORDS:]

        if len(games) != original_count:
            self._data["games"] = games
            _LOGGER.info(
                "Pruned analytics: %d -> %d games",
                original_count,
                len(games),
            )

    def get_games(
        self, start_date: int | None = None, end_date: int | None = None
    ) -> list[GameRecord]:
        """Get game records filtered by date range."""
        games = self._data["games"]
        if start_date is not None:
            games = [g for g in games if g["ended_at"] >= start_date]
        if end_date is not None:
            games = [g for g in games if g["ended_at"] <= end_date]
        return games

    @property
    def total_games(self) -> int:
        """Get total games recorded."""
        return len(self._data["games"])

    def compute_metrics(self, period: str = "30d") -> dict[str, Any]:
        """Compute dashboard metrics for a given period.

        Results are memoized per ``period`` (#418): the game history only
        changes at game end / prune, so a fingerprint of ``(total_games,
        last ended_at)`` lets repeated reads reuse the last result instead of
        re-scanning every record. ``add_game`` clears the cache; a prune shifts
        the fingerprint, so both paths recompute.

        #473: the fingerprint also carries a coarse hourly wall-clock bucket
        (``int(time.time()) // 3600``). The underlying compute is wall-clock
        relative — the window is ``now - days*86400`` and the chart buckets are
        relative to ``now`` — so with no new game the games-only fingerprint
        would never change and the 7d/30d results plus chart would freeze on the
        cache-write day (aged-out games still counted). The hourly bucket makes
        the windowed metrics expire as wall-clock advances.
        """
        games = self._data["games"]
        fingerprint = (
            len(games),
            games[-1]["ended_at"] if games else 0,
            int(time.time()) // 3600,
        )
        cached = self._metrics_cache.get(period)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

        result = self._compute_metrics_uncached(period)
        self._metrics_cache[period] = (fingerprint, result)
        return result

    def _compute_metrics_uncached(self, period: str) -> dict[str, Any]:
        """Compute dashboard metrics for a given period (no memoization)."""
        now = int(time.time())
        days_map = {"7d": 7, "30d": 30, "90d": 90, "all": 365 * 10}
        days = days_map.get(period, 30)
        current_start = now - (days * 86400)

        current_games = self.get_games(start_date=current_start, end_date=now)

        total_games = len(current_games)
        total_players = sum(g["player_count"] for g in current_games)
        total_rounds = sum(g["rounds_played"] for g in current_games)

        avg_players = total_players / total_games if total_games > 0 else 0

        # Top players across all games
        player_totals: dict[str, int] = {}
        for game in current_games:
            for name, score in game.get("player_scores", {}).items():
                player_totals[name] = player_totals.get(name, 0) + score

        top_players = sorted(player_totals.items(), key=lambda x: -x[1])[:10]

        # Category stats
        cat_stats: dict[str, dict[str, Any]] = {}
        for game in current_games:
            cat = game.get("category", "mixed")
            if cat not in cat_stats:
                cat_stats[cat] = {"games": 0, "total_score": 0, "total_players": 0}
            cat_stats[cat]["games"] += 1
            cat_stats[cat]["total_score"] += (
                game.get("average_score", 0) * game["player_count"]
            )
            cat_stats[cat]["total_players"] += game["player_count"]

        category_list = []
        for cat, stats in cat_stats.items():
            avg_score = (
                stats["total_score"] / stats["total_players"]
                if stats["total_players"] > 0
                else 0
            )
            category_list.append({
                "category": cat,
                "games_played": stats["games"],
                "avg_score": round(avg_score, 1),
            })

        # Games over time
        chart_data = self._compute_games_over_time(current_games, period)

        return {
            "period": period,
            "total_games": total_games,
            "avg_players_per_game": round(avg_players, 1),
            "total_rounds": total_rounds,
            "top_players": [{"name": n, "total_score": s} for n, s in top_players],
            "category_stats": category_list,
            "chart_data": chart_data,
            "recent_games": [
                {
                    "game_id": g["game_id"],
                    "date": datetime.fromtimestamp(
                        g["ended_at"], tz=UTC
                    ).strftime("%Y-%m-%d %H:%M"),
                    "category": g.get("category", "mixed"),
                    "player_count": g["player_count"],
                    "rounds_played": g["rounds_played"],
                    "winner": g.get("winner", ""),
                    "duration": g.get("duration_seconds", 0),
                }
                for g in sorted(
                    current_games, key=lambda g: g["ended_at"], reverse=True
                )[:20]
            ],
            "generated_at": now,
        }

    def _compute_games_over_time(
        self, games: list[GameRecord], period: str
    ) -> dict[str, Any]:
        """Aggregate game counts for chart visualization."""
        from datetime import timedelta  # noqa: PLC0415

        now = datetime.now(UTC)

        if period == "7d":
            days = 7
            buckets = {
                (now - timedelta(days=i)).strftime("%Y-%m-%d"): 0 for i in range(days)
            }
            for game in games:
                dt = datetime.fromtimestamp(game["ended_at"], tz=UTC)
                key = dt.strftime("%Y-%m-%d")
                if key in buckets:
                    buckets[key] += 1
            labels = [
                (now - timedelta(days=i)).strftime("%a")
                for i in range(days - 1, -1, -1)
            ]
            values = [
                buckets[(now - timedelta(days=i)).strftime("%Y-%m-%d")]
                for i in range(days - 1, -1, -1)
            ]
        elif period in ("30d", "90d"):
            weeks = 4 if period == "30d" else 13
            week_buckets: dict[str, int] = {}
            for i in range(weeks):
                week_start = now - timedelta(days=now.weekday() + 7 * i)
                week_buckets[week_start.strftime("%Y-%m-%d")] = 0
            for game in games:
                dt = datetime.fromtimestamp(game["ended_at"], tz=UTC)
                week_start = dt - timedelta(days=dt.weekday())
                key = week_start.strftime("%Y-%m-%d")
                if key in week_buckets:
                    week_buckets[key] += 1
            sorted_keys = sorted(week_buckets.keys())
            labels = [f"W{i + 1}" for i in range(len(sorted_keys))]
            values = [week_buckets[k] for k in sorted_keys]
        else:
            month_buckets: dict[str, int] = {}
            for game in games:
                dt = datetime.fromtimestamp(game["ended_at"], tz=UTC)
                key = dt.strftime("%Y-%m")
                month_buckets[key] = month_buckets.get(key, 0) + 1
            sorted_keys = sorted(month_buckets.keys())[-12:]
            labels = (
                [datetime.strptime(k, "%Y-%m").strftime("%b") for k in sorted_keys]
                if sorted_keys else []
            )
            values = [month_buckets[k] for k in sorted_keys] if sorted_keys else []

        return {"labels": labels, "values": values}
