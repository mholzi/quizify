"""Issue #650 — the countdown pulse has to start with the digit.

Reported from live play on v1.10.0-RC1: the number pulses, but not in time
with the number. The two were never able to agree. The digit is rewritten by
a one-second interval; the pulse was a CSS animation declared `infinite` at
half a second, so it beat twice per second with a phase set by whichever tick
first added the critical class, and `setInterval` drift pushed them further
apart as the question ran. The audible tick rides the same interval as the
digit, so sound and animation openly disagreed about when a second was.

The fix binds the pulse to the tick that writes the digit: one shot per
update, restarted from JS. These tests pin the two halves that make that
work — a non-infinite animation in the CSS, and a retrigger that survives the
browser's tendency to collapse remove+add into nothing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_PLAYER_JS = (WWW / "js" / "player-game.js").read_text(encoding="utf-8")
_CSS_SRC = (WWW / "css" / "src" / "07-player.css").read_text(encoding="utf-8")
_CSS_BUILT = (WWW / "css" / "styles.css").read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """Return the declaration block for *selector*, or '' if absent."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return match.group(1) if match else ""


class TestThePulseIsNoLongerFreeRunning:
    def test_critical_state_carries_no_animation(self) -> None:
        """The urgency colour must not drag a self-running animation with it.

        `.timer--critical` is added once, on the first tick at or below five
        seconds, and then stays. Any animation declared on it therefore runs
        on its own clock for the rest of the question — which is exactly the
        defect.
        """
        assert "animation" not in _rule(_CSS_SRC, ".timer--critical")

    def test_the_pulse_runs_once_per_trigger(self) -> None:
        rule = _rule(_CSS_SRC, ".timer--tick")
        assert "timer-pulse-intense" in rule
        assert "infinite" not in rule

    def test_the_pulse_finishes_before_the_next_digit(self) -> None:
        """A pulse longer than a second would still be running when the next
        one starts, which re-creates the overlap the fix removes."""
        rule = _rule(_CSS_SRC, ".timer--tick")
        seconds = re.search(r"animation:[^;]*?([\d.]+)s", rule)
        assert seconds, rule
        assert float(seconds.group(1)) < 1.0

    def test_the_built_stylesheet_has_it(self) -> None:
        """styles.css is committed; a forgotten rebuild ships a dead class."""
        assert ".timer--tick" in _CSS_BUILT, "run scripts/build_css.py"
        assert "animation" not in _rule(_CSS_BUILT, ".timer--critical")


class TestTheRetriggerIsBoundToTheDigit:
    def test_the_pulse_fires_from_the_same_update_as_the_digit(self) -> None:
        """`pulseTimer` has to be called on the branch that renders the
        critical state, not on entry into it — one pulse per digit is the
        whole point."""
        body = re.search(
            r"if \(remaining <= 5\) \{(.*?)\} else if", _PLAYER_JS, re.S
        )
        assert body, "the critical branch moved — re-check the binding"
        assert "pulseTimer(timerElement)" in body.group(1)

    def test_the_reflow_between_remove_and_add_is_there(self) -> None:
        """Without it the browser coalesces both class changes into no change
        and the animation keeps running — the fix would silently do nothing.

        Asserted on the read of `offsetWidth` between the two calls rather
        than on a comment, because only the read forces the layout.
        """
        body = re.search(
            r"function pulseTimer\(el\) \{(.*?)\n    \}", _PLAYER_JS, re.S
        )
        assert body, "pulseTimer is gone"
        order = body.group(1)
        assert order.index("classList.remove") < order.index("offsetWidth")
        assert order.index("offsetWidth") < order.index("classList.add")


class TestTheClassDoesNotOutliveTheUrgency:
    def test_a_fresh_question_starts_clean(self) -> None:
        """A question that begins above five seconds must not inherit a pulse
        left over from the last one."""
        start = re.search(
            r"function startCountdown\(deadline\) \{(.*?)function updateCountdown",
            _PLAYER_JS,
            re.S,
        )
        assert start
        assert "'timer--tick'" in start.group(1)

    def test_leaving_the_critical_range_drops_it(self) -> None:
        """Only reachable when the deadline moves (a paused or extended
        round), but a stuck pulse on a calm timer would be worse than the
        bug being fixed."""
        for branch in (
            r"\} else if \(remaining <= 10\) \{(.*?)\} else \{",
            r"\} else \{(.*?)\n            \}",
        ):
            body = re.search(branch, _PLAYER_JS, re.S)
            assert body, branch
            assert "timer--tick" in body.group(1)
