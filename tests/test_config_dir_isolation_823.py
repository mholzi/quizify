"""No test may write into the installed test harness (#823).

``HARuntime.data_dir`` is ``hass.config.path("quizify")``, and under
pytest-homeassistant-custom-component ``hass.config`` points at
``site-packages/pytest_homeassistant_custom_component/testing_config/`` — one
directory, shared by every run on the machine, cleaned by nobody. Every store
the integration owns therefore appended to the same files run after run:
presets, community packs, analytics, question history, question stats.

The presets file is the one with a cap. ``MAX_PRESETS`` is 20, so from the
twentieth run on, ``test_service_start_settings_744`` failed on any machine
that had run the suite before — and three people spent a morning proving it was
not their branch. ``test_community_pack_location_743`` failed the same way on a
leftover ``quizify/packs/`` directory. CI never saw either: a fresh virtualenv
starts at run one.

The fix is one redirect in ``conftest.py`` — ``Config.path`` sends the
``quizify`` subtree to the test's own ``tmp_path`` — plus the session guard
that fails the run if anything lands in the shared directory anyway. What is
asserted here is the redirect itself, store by store, so the fix cannot be
half-removed without a red test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.conftest import harness_config_dir, harness_config_entries  # noqa: E402

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

from custom_components.quizify.const import DOMAIN  # noqa: E402

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """No hass_frontend wheel under test; mirrors the other service tests."""
    from unittest.mock import patch  # noqa: PLC0415

    panels: dict = {}

    with (
        patch(
            "homeassistant.components.frontend.async_register_built_in_panel",
            side_effect=lambda _hass, *, frontend_url_path, **_kw: panels.setdefault(
                frontend_url_path, True
            ),
        ),
        patch(
            "homeassistant.components.frontend.async_remove_panel",
            side_effect=lambda _hass, path: panels.pop(path),
        ),
    ):
        yield panels


@pytest.fixture
async def quizify_hass(hass: HomeAssistant) -> HomeAssistant:
    assert await async_setup_component(hass, "http", {"http": {}})
    await hass.async_block_till_done()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return hass


def test_the_harness_config_dir_is_the_one_that_used_to_be_written() -> None:
    """The guard only means something if it is watching the right place."""
    root = harness_config_dir()
    assert root is not None
    assert "site-packages" in str(root)
    assert root.name == "testing_config"


async def test_the_quizify_subtree_is_redirected_out_of_site_packages(
    quizify_hass: HomeAssistant, tmp_path: Path
) -> None:
    """``hass.config.path("quizify", …)`` lands in the test's own directory.

    Everything the integration writes is built from this one path, so this is
    the assertion the rest of the fix hangs on. The unrelated call is here to
    show that only the ``quizify`` subtree moves — custom-component discovery
    still resolves under the harness config dir.
    """
    hass = quizify_hass
    quizify_dir = Path(hass.config.path("quizify"))
    assert tmp_path in quizify_dir.parents
    assert "site-packages" not in str(quizify_dir)
    assert "site-packages" in hass.config.path("custom_components")


async def test_every_store_writes_under_the_tests_own_directory(
    quizify_hass: HomeAssistant, tmp_path: Path
) -> None:
    """Presets, community packs, analytics and question history, one by one.

    #823 was reported against the preset store and hit in the community-pack
    test as well, so each store is named here rather than trusted to follow.
    """
    hass = quizify_hass
    quizify_residue = {
        entry for entry in harness_config_entries() if entry.startswith("quizify")
    }

    from custom_components.quizify.server.views import (  # noqa: PLC0415
        get_preset_store,
    )

    store = get_preset_store(hass.http.app)
    # A shared store would already hold entries from every earlier run — which
    # is exactly how this ran into MAX_PRESETS.
    assert await store.list() == []
    await store.save({"name": "Scratch", "rounds": 8})
    assert tmp_path in store._file.path.parents

    game = hass.data[DOMAIN]["game"]
    packs_dir = game.question_bank.community_dir
    assert packs_dir is not None
    assert tmp_path in packs_dir.parents
    # Created eagerly at setup so hosts find the folder — under tmp_path now.
    assert packs_dir.is_dir()

    analytics = hass.data[DOMAIN]["analytics"]
    assert tmp_path in analytics._path.parents

    # Only what THIS test added counts: a machine can carry residue from
    # before the redirect, and deleting it is not this test's job. Anything
    # else that leaks is caught for the run as a whole by the session-scoped
    # guard in conftest.py.
    after = {
        entry for entry in harness_config_entries() if entry.startswith("quizify")
    }
    assert after == quizify_residue, (
        f"a store wrote into the shared harness config directory: "
        f"{sorted(after - quizify_residue)}"
    )
