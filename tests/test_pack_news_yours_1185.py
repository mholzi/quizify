"""Pack news: chips that pick the pack, and "your pack is in" (#1185, P7/P8).

P7: the banner named a new pack and sent the host off to find it in the list
below. The names are chips now; a tap selects the pack.

P8: whether a submitted pack was accepted lived only in the submission
timeline, deep in the setup. The banner now says it once: "Your pack 'Vulkane'
was accepted. Thank you!", and when it arrives with an update, "… is in the
game now". Once each — dismissing the banner stamps what was said.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.server.pack_submission import (  # noqa: E402
    PackSubmissionStore,
)

_JS = _REPO / "custom_components" / "quizify" / "www" / "js"


class _Runtime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def run_in_executor(self, func, *args) -> Any:
        return await asyncio.get_running_loop().run_in_executor(None, func, *args)


class _Ctx:
    def __init__(self, data_dir: Path) -> None:
        self.runtime = _Runtime(data_dir)


def _seed(tmp_path: Path, subs: list[dict]) -> PackSubmissionStore:
    (tmp_path / "pack_submissions.json").write_text(
        json.dumps({"submissions": subs}), "utf-8"
    )
    return PackSubmissionStore(_Ctx(tmp_path))


_VULKANE = {"id": "1", "kind": "submission", "name": "Vulkane", "language": "de",
            "status": "accepted", "issue_number": 5}


@pytest.mark.asyncio
async def test_an_accepted_pack_is_named_once(tmp_path: Path) -> None:
    store = _seed(tmp_path, [_VULKANE, {**_VULKANE, "id": "2", "name": "Wüsten", "status": "pending"}])
    yours = await store.your_pack_news([])
    assert yours == [{"id": "1", "name": "Vulkane", "state": "accepted"}]
    await store.mark_news_seen(yours)
    assert await store.your_pack_news([]) == []


@pytest.mark.asyncio
async def test_it_is_named_again_when_it_arrives(tmp_path: Path) -> None:
    store = _seed(tmp_path, [{**_VULKANE, "news_seen": ["accepted"]}])
    news = [{"slug": "vulkane-de", "name": "Vulkane", "language": "de", "question_count": 40}]
    yours = await store.your_pack_news(news)
    assert yours == [{"id": "1", "name": "Vulkane", "state": "arrived"}]
    await store.mark_news_seen(yours)
    assert await store.your_pack_news(news) == []
    # and never falls back to "accepted" after it arrived
    assert await store.your_pack_news([]) == []


@pytest.mark.asyncio
async def test_requests_and_declined_packs_are_not_news(tmp_path: Path) -> None:
    store = _seed(tmp_path, [
        {**_VULKANE, "kind": "request"},
        {**_VULKANE, "id": "3", "status": "declined"},
    ])
    assert await store.your_pack_news([]) == []


def test_the_banner_chips_select_the_pack() -> None:
    src = (_JS / "admin.js").read_text("utf-8")
    render = src[src.index("function renderPackNewsNames(lang) {") :]
    render = render[: render.index("\n}\n")]
    assert 'class="pack-news-chip" data-slug="' in render
    assert "selectPackFromNews(chip.getAttribute('data-slug'))" in src
    # The second sentence ("pick it from the category list") is gone.
    assert "packNewsBody" not in src
