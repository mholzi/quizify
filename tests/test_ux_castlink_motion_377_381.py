"""Guard tests for two self-contained UX fixes.

#377 — Cast-to-TV link dead in the Android HA Companion WebView (which
       swallows target="_blank"). admin.js must detect the Companion and
       navigate window.location to the dashboard instead.
#381 — dashboard.html (self-contained, no styles.css) ignored the OS
       prefers-reduced-motion setting on the always-on TV.

These are static-source assertions: the frontend assets are not bundled,
so we read them as text.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"


def test_admin_js_intercepts_cast_link_on_android_companion() -> None:
    """#377: admin.js must port the isAndroidCompanion workaround and, on
    Companion Android, navigate to the dashboard instead of relying on the
    dead target="_blank" Cast-to-TV link."""
    admin_js = (_WWW / "js" / "admin.js").read_text(encoding="utf-8")
    assert "isAndroidCompanion" in admin_js
    # The helper must be wired to a click handler on the dashboard link.
    assert "dashboardLink" in admin_js
    assert "addEventListener('click'" in admin_js
    assert "preventDefault" in admin_js
    assert "/quizify/dashboard" in admin_js


def test_dashboard_html_honors_prefers_reduced_motion() -> None:
    """#381: dashboard.html's inline <style> must carry a reduced-motion
    override block, since it does not load styles.css."""
    dashboard = (_WWW / "dashboard.html").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in dashboard
    # Standard house-style override values (see css/src/00-tokens.css).
    assert "animation-duration: 0.01ms !important" in dashboard
    assert "animation-iteration-count: 1 !important" in dashboard
    assert "transition-duration: 0.01ms !important" in dashboard
