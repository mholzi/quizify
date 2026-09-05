"""Community packs survive a HACS update, and can be reloaded (issue #743).

Two defects, one story:

* ``QUESTIONS_DIR`` lives inside the integration, so ``questions/community``
  — the only folder the docs ever named — is inside the directory HACS
  replaces wholesale on every update. Every pack the host dropped there was
  deleted, without a word, by the next update.
* ``reload_categories()`` existed but had no caller and no service, so the
  "in-app pack reload" the README promised did not exist either: the only way
  to pick up a new pack was a full Home Assistant restart.

The fix moves the host-owned drop-in folder to ``<config>/quizify/packs``
(outside ``custom_components``), migrates whatever is still sitting in the old
folder into it, keeps scanning the old folder for the shipped example pack and
for anything a migration could not move, and wires ``reload_categories`` to a
``quizify.reload_packs`` service.

Unit level covers the loader + migration; integration level (real ``hass``
fixture, like ``test_game_control_services_367``) covers the service.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import DOMAIN  # noqa: E402
from custom_components.quizify.game.questions import (  # noqa: E402
    COMMUNITY_PACKS_DIRNAME,
    COMMUNITY_SUBDIR,
    SHIPPED_COMMUNITY_PACKS,
    QuestionBank,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pack(name: str, *, prefix: str = "q", count: int = 3) -> dict:
    """A minimal, valid community pack payload."""
    return {
        "name": name,
        "language": "en",
        "version": "1.0",
        "questions": [
            {
                "id": f"{prefix}_{i}",
                "question": f"Question {i}?",
                "answers": [
                    {"text": "Right", "correct": True},
                    {"text": "Wrong", "correct": False},
                    {"text": "Also wrong", "correct": False},
                ],
                "difficulty": "easy",
            }
            for i in range(count)
        ],
    }


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """(questions_dir, community_dir) — the shipped tree and the host's tree.

    ``questions_dir`` stands in for ``custom_components/quizify/questions``
    (replaced by every HACS update); ``community_dir`` for
    ``<config>/quizify/packs`` (the host's own, untouched by updates).
    """
    questions_dir = tmp_path / "custom_components" / "quizify" / "questions"
    questions_dir.mkdir(parents=True)
    community_dir = tmp_path / "config" / "quizify" / COMMUNITY_PACKS_DIRNAME
    return questions_dir, community_dir


# ---------------------------------------------------------------------------
# Unit level: the drop-in folder lives outside the integration
# ---------------------------------------------------------------------------


def test_pack_in_config_dir_is_loaded(tmp_path: Path) -> None:
    """A pack dropped into <config>/quizify/packs is discovered — the folder
    HACS cannot touch is a real scan root, not just documentation."""
    questions_dir, community_dir = _make_dirs(tmp_path)
    _write(community_dir / "birthday.json", _pack("Birthday Quiz"))

    bank = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    bank.load_all_categories()

    assert "community-birthday" in bank.get_categories()
    assert bank.get_pack_versions()["community-birthday"]["name"] == "Birthday Quiz"


def test_config_dir_packs_survive_an_integration_wipe(tmp_path: Path) -> None:
    """The regression itself: wipe the whole integration questions tree (what
    a HACS update does) and the host's pack is still there afterwards."""
    questions_dir, community_dir = _make_dirs(tmp_path)
    host_pack = _write(community_dir / "family.json", _pack("Family Quiz"))
    # Something the host dropped in the *old* place, plus the shipped example.
    _write(questions_dir / COMMUNITY_SUBDIR / "example-pack.json", _pack("Example"))

    bank = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    bank.load_all_categories()
    assert "community-family" in bank.get_categories()

    # HACS replaces custom_components/quizify wholesale.
    import shutil

    shutil.rmtree(questions_dir)
    questions_dir.mkdir(parents=True)
    _write(questions_dir / COMMUNITY_SUBDIR / "example-pack.json", _pack("Example"))

    assert host_pack.is_file(), "the host's pack must survive the update"
    fresh = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    fresh.load_all_categories()
    assert "community-family" in fresh.get_categories()


def test_legacy_folder_is_still_scanned(tmp_path: Path) -> None:
    """The shipped example pack lives in questions/community and keeps
    loading — the new root is added, the old one is not dropped."""
    questions_dir, community_dir = _make_dirs(tmp_path)
    _write(questions_dir / COMMUNITY_SUBDIR / "example-pack.json", _pack("Example"))

    bank = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    bank.load_all_categories()

    assert "community-example-pack" in bank.get_categories()


def test_bank_without_runtime_falls_back_to_legacy_folder(tmp_path: Path) -> None:
    """A bank built without a community_dir (bare unit tests, standalone) is
    unchanged: it scans only the in-integration folder and migrates nothing."""
    questions_dir, _ = _make_dirs(tmp_path)
    legacy = _write(
        questions_dir / COMMUNITY_SUBDIR / "mine.json", _pack("Mine")
    )

    bank = QuestionBank(questions_dir=questions_dir)
    bank.load_all_categories()

    assert bank.community_dir is None
    assert "community-mine" in bank.get_categories()
    assert legacy.is_file()


# ---------------------------------------------------------------------------
# Unit level: migration out of the doomed folder
# ---------------------------------------------------------------------------


def test_legacy_host_pack_is_migrated_to_config_dir(tmp_path: Path) -> None:
    """A host who already had a pack in questions/community keeps it: the file
    is MOVED into <config>/quizify/packs on the next load."""
    questions_dir, community_dir = _make_dirs(tmp_path)
    legacy = _write(
        questions_dir / COMMUNITY_SUBDIR / "party.json", _pack("Party Quiz")
    )

    bank = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    bank.load_all_categories()

    assert not legacy.exists(), "the pack must leave the folder HACS replaces"
    moved = community_dir / "party.json"
    assert moved.is_file()
    assert json.loads(moved.read_text(encoding="utf-8"))["name"] == "Party Quiz"
    # And it is still loaded, from its new home.
    assert "community-party" in bank.get_categories()


def test_shipped_example_pack_is_not_migrated(tmp_path: Path) -> None:
    """Files the integration ships are release artefacts, not host data — they
    stay put, so the migration never copies repo content into the config dir
    (and never re-creates it after every update)."""
    questions_dir, community_dir = _make_dirs(tmp_path)
    assert "example-pack.json" in SHIPPED_COMMUNITY_PACKS
    shipped = _write(
        questions_dir / COMMUNITY_SUBDIR / "example-pack.json", _pack("Example")
    )

    bank = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    bank.load_all_categories()

    assert shipped.is_file()
    assert not (community_dir / "example-pack.json").exists()
    assert "community-example-pack" in bank.get_categories()


def test_migration_never_overwrites_an_existing_host_pack(tmp_path: Path) -> None:
    """Same filename in both places: the host's copy in the config dir wins and
    is not clobbered, and the leftover is not loaded twice."""
    questions_dir, community_dir = _make_dirs(tmp_path)
    _write(community_dir / "dupe.json", _pack("Newer", prefix="new"))
    legacy = _write(
        questions_dir / COMMUNITY_SUBDIR / "dupe.json", _pack("Older", prefix="old")
    )

    bank = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    bank.load_all_categories()

    assert legacy.is_file(), "never delete the old copy we could not move"
    assert json.loads(
        (community_dir / "dupe.json").read_text(encoding="utf-8")
    )["name"] == "Newer"
    assert bank.get_pack_versions()["community-dupe"]["name"] == "Newer"
    assert len(bank.categories["community-dupe"]) == 3


def test_failed_migration_still_loads_the_pack(tmp_path: Path) -> None:
    """If the move fails (read-only mount, permissions), the pack is still
    loaded from the legacy folder — a failed migration degrades to the old
    behaviour instead of losing the questions."""
    questions_dir, community_dir = _make_dirs(tmp_path)
    legacy = _write(
        questions_dir / COMMUNITY_SUBDIR / "stuck.json", _pack("Stuck")
    )

    bank = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    with patch(
        "custom_components.quizify.game.questions.shutil.move",
        side_effect=OSError("read-only file system"),
    ):
        bank.load_all_categories()

    assert legacy.is_file()
    assert "community-stuck" in bank.get_categories()


# ---------------------------------------------------------------------------
# Unit level: reload actually reloads
# ---------------------------------------------------------------------------


def test_reload_picks_up_a_newly_dropped_pack(tmp_path: Path) -> None:
    """The point of the reload path: drop a pack after the bank was loaded and
    reload_categories() finds it — the _loaded short-circuit does not win."""
    questions_dir, community_dir = _make_dirs(tmp_path)
    community_dir.mkdir(parents=True, exist_ok=True)

    bank = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    bank.load_all_categories()
    assert "community-late" not in bank.get_categories()

    _write(community_dir / "late.json", _pack("Late Arrival"))
    bank.reload_categories()

    assert "community-late" in bank.get_categories()
    assert bank.get_pack_versions()["community-late"]["name"] == "Late Arrival"


def test_force_defeats_the_loaded_short_circuit(tmp_path: Path) -> None:
    """``load_all_categories(force=True)`` re-reads even when the bank thinks
    it is loaded — the short-circuit is evaluated inside the method, so a
    caller cannot end up with a silently skipped reload."""
    questions_dir, community_dir = _make_dirs(tmp_path)
    community_dir.mkdir(parents=True, exist_ok=True)

    bank = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    bank.load_all_categories()
    assert bank.is_loaded is True

    _write(community_dir / "forced.json", _pack("Forced"))
    assert "community-forced" not in bank.load_all_categories()
    assert "community-forced" in bank.load_all_categories(force=True)


def test_reload_drops_a_removed_pack_from_the_metadata(tmp_path: Path) -> None:
    """A deleted pack disappears from get_pack_versions() too, not just from
    the questions — the admin chips and /api/quizify/packs are built from that
    metadata, so a stale entry would keep offering a pack that is gone."""
    questions_dir, community_dir = _make_dirs(tmp_path)
    pack = _write(community_dir / "temporary.json", _pack("Temporary"))

    bank = QuestionBank(questions_dir=questions_dir, community_dir=community_dir)
    bank.load_all_categories()
    assert "community-temporary" in bank.get_pack_versions()

    pack.unlink()
    bank.reload_categories()

    assert "community-temporary" not in bank.get_categories()
    assert "community-temporary" not in bank.get_pack_versions()


# ---------------------------------------------------------------------------
# services.yaml / translations
# ---------------------------------------------------------------------------


def test_reload_packs_is_declared_in_services_yaml() -> None:
    """The service is declared, so it shows up in Developer Tools → Actions
    with a name and a description rather than as a bare domain.service."""
    yaml = pytest.importorskip("yaml")
    path = _REPO_ROOT / "custom_components" / "quizify" / "services.yaml"
    declared = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "reload_packs" in declared
    assert declared["reload_packs"]["name"]
    assert declared["reload_packs"]["description"]


def test_reload_packs_has_strings_in_every_locale() -> None:
    """Every locale that carries service strings carries this one too."""
    base = _REPO_ROOT / "custom_components" / "quizify" / "translations"
    for path in sorted(base.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        services = data.get("services")
        if not services:
            continue
        assert "reload_packs" in services, f"{path.name} misses reload_packs"
        assert services["reload_packs"]["name"]
        assert services["reload_packs"]["description"]


def test_community_readme_points_at_the_config_dir() -> None:
    """The docs and the code finally agree on where a host's packs go."""
    readme = (
        _REPO_ROOT
        / "custom_components"
        / "quizify"
        / "questions"
        / COMMUNITY_SUBDIR
        / "README.md"
    )
    text = readme.read_text(encoding="utf-8")
    assert f"quizify/{COMMUNITY_PACKS_DIRNAME}" in text
    assert "quizify.reload_packs" in text


# ---------------------------------------------------------------------------
# Integration level: the reload service
# ---------------------------------------------------------------------------

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import ServiceValidationError  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

from custom_components.quizify.game.state import GamePhase  # noqa: E402

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def _clean_config_packs_dir():
    """Keep the shared HA test config directory free of leftover packs.

    ``pytest_homeassistant_custom_component`` hands every ``hass`` fixture the
    same on-disk config directory, so a pack written by one test would still be
    there for the next run (and for every other test in the suite). Wipe the
    drop-in folder around each test.
    """
    from pytest_homeassistant_custom_component.common import (  # noqa: PLC0415
        get_test_config_dir,
    )

    packs = Path(get_test_config_dir()) / "quizify" / COMMUNITY_PACKS_DIRNAME

    def _wipe() -> None:
        if packs.is_dir():
            for leftover in packs.glob("*.json"):
                leftover.unlink()

    _wipe()
    yield
    _wipe()


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Stub the frontend panel helpers (the hass_frontend wheel is absent under
    the test harness). Mirrors test_game_control_services_367."""
    panels: dict = {}

    def _register_panel(_hass, *, frontend_url_path, **_kw):
        panels[frontend_url_path] = True

    def _remove_panel(_hass, path):
        if path not in panels:
            raise KeyError(path)
        del panels[path]

    with (
        patch(
            "homeassistant.components.frontend.async_register_built_in_panel",
            side_effect=_register_panel,
        ),
        patch(
            "homeassistant.components.frontend.async_remove_panel",
            side_effect=_remove_panel,
        ),
    ):
        yield panels


@pytest.fixture
async def http_hass(hass: HomeAssistant) -> HomeAssistant:
    """A hass with the HTTP component set up (setup mounts routes on it)."""
    assert await async_setup_component(hass, "http", {"http": {}})
    await hass.async_block_till_done()
    return hass


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return entry


async def test_reload_packs_service_registered(http_hass: HomeAssistant) -> None:
    """Setup registers quizify.reload_packs."""
    hass = http_hass
    await _setup(hass)
    assert hass.services.has_service(DOMAIN, "reload_packs")


async def test_bank_reads_the_config_dir_under_hass(
    http_hass: HomeAssistant,
) -> None:
    """Wired through the runtime: the bank's drop-in folder is
    <config>/quizify/packs, not a folder inside the integration."""
    hass = http_hass
    await _setup(hass)
    bank = hass.data[DOMAIN]["game"].question_bank
    assert bank.community_dir == Path(
        hass.config.path("quizify", COMMUNITY_PACKS_DIRNAME)
    )
    assert "custom_components" not in str(bank.community_dir)


async def test_reload_packs_service_picks_up_a_new_pack(
    http_hass: HomeAssistant,
) -> None:
    """The whole point: drop a pack into the config folder on a running HA,
    call the service, and the pack is available — no restart."""
    hass = http_hass
    await _setup(hass)
    bank = hass.data[DOMAIN]["game"].question_bank
    assert "community-tonight" not in bank.get_categories()

    target = Path(hass.config.path("quizify", COMMUNITY_PACKS_DIRNAME))
    await hass.async_add_executor_job(
        _write, target / "tonight.json", _pack("Tonight's Pack")
    )

    await hass.services.async_call(DOMAIN, "reload_packs", {}, blocking=True)
    await hass.async_block_till_done()

    assert "community-tonight" in bank.get_categories()
    assert bank.get_pack_versions()["community-tonight"]["name"] == (
        "Tonight's Pack"
    )


async def test_reload_packs_refused_mid_game(http_hass: HomeAssistant) -> None:
    """Reloading under a running game would swap the bank beneath the queue the
    players are answering, so it is refused with a clear message."""
    hass = http_hass
    await _setup(hass)
    game = hass.data[DOMAIN]["game"]
    game.start_game(num_rounds=10, lightning_enabled=False)
    game.start_next_question()
    assert game.phase == GamePhase.QUESTION_ACTIVE

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "reload_packs", {}, blocking=True)

    hass.data[DOMAIN]["ws_handler"]._cancel_timer_tick()


async def test_reload_packs_without_setup_raises_validation_error(
    http_hass: HomeAssistant,
) -> None:
    """After the entry is unloaded the service raises ServiceValidationError,
    not a raw KeyError."""
    hass = http_hass
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "reload_packs", {}, blocking=True)
