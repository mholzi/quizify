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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

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


class AnalyticsData(TypedDict):
    """Complete analytics data schema."""

    version: int
    games: list[GameRecord]


class QuizifyAnalytics:
    """Analytics storage with async file I/O and atomic writes."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize analytics storage."""
        self._hass = hass
        self._path = Path(hass.config.path("quizify", "analytics.json"))
        self._data: AnalyticsData = self._empty_data()
        self._games_since_prune = 0
        self._save_lock = asyncio.Lock()

    def _empty_data(self) -> AnalyticsData:
        """Return empty analytics data structure."""
        return {"version": 1, "games": []}

    async def load(self) -> None:
        """Load analytics data from file."""
        try:
            if self._path.exists():
                content = await self._hass.async_add_executor_job(self._path.read_text)
                self._data = json.loads(content)
                _LOGGER.debug("Loaded analytics: %d games", len(self._data.get("games", [])))
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
                await self._hass.async_add_executor_job(
                    self._path.parent.mkdir, 0o755, True, True
                )
                temp_path = self._path.with_suffix(".tmp")
                content = json.dumps(self._data, indent=2)

                def _write_atomic() -> None:
                    temp_path.write_text(content)
                    os.replace(temp_path, self._path)

                await self._hass.async_add_executor_job(_write_atomic)
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
    ) -> None:
        """Record a completed game."""
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
        _LOGGER.info(
            "Game %s recorded: %d players, %d rounds, winner=%s",
            game_id,
            player_count,
            num_rounds,
            winner,
        )

    async def add_game(self, record: GameRecord) -> None:
        """Add game record and schedule save."""
        self._data["games"].append(record)
        self._games_since_prune += 1

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
        """Compute dashboard metrics for a given period."""
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
            cat_stats[cat]["total_score"] += game.get("average_score", 0) * game["player_count"]
            cat_stats[cat]["total_players"] += game["player_count"]

        category_list = []
        for cat, stats in cat_stats.items():
            avg_score = stats["total_score"] / stats["total_players"] if stats["total_players"] > 0 else 0
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
                    "date": datetime.fromtimestamp(g["ended_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "category": g.get("category", "mixed"),
                    "player_count": g["player_count"],
                    "rounds_played": g["rounds_played"],
                    "winner": g.get("winner", ""),
                    "duration": g.get("duration_seconds", 0),
                }
                for g in sorted(current_games, key=lambda g: g["ended_at"], reverse=True)[:20]
            ],
            "generated_at": now,
        }

    def _compute_games_over_time(
        self, games: list[GameRecord], period: str
    ) -> dict[str, Any]:
        """Aggregate game counts for chart visualization."""
        from datetime import timedelta  # noqa: PLC0415

        now = datetime.now(timezone.utc)

        if period == "7d":
            days = 7
            buckets = {
                (now - timedelta(days=i)).strftime("%Y-%m-%d"): 0 for i in range(days)
            }
            for game in games:
                dt = datetime.fromtimestamp(game["ended_at"], tz=timezone.utc)
                key = dt.strftime("%Y-%m-%d")
                if key in buckets:
                    buckets[key] += 1
            labels = [
                (now - timedelta(days=i)).strftime("%a") for i in range(days - 1, -1, -1)
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
                dt = datetime.fromtimestamp(game["ended_at"], tz=timezone.utc)
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
                dt = datetime.fromtimestamp(game["ended_at"], tz=timezone.utc)
                key = dt.strftime("%Y-%m")
                month_buckets[key] = month_buckets.get(key, 0) + 1
            sorted_keys = sorted(month_buckets.keys())[-12:]
            labels = (
                [datetime.strptime(k, "%Y-%m").strftime("%b") for k in sorted_keys]
                if sorted_keys else []
            )
            values = [month_buckets[k] for k in sorted_keys] if sorted_keys else []

        return {"labels": labels, "values": values}
