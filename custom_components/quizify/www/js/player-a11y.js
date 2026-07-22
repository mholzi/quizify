/**
 * Quizify Player - Accessibility Mode (#372)
 *
 * One opt-in comfort mode for the player's own phone: larger text and
 * motion held still. Persisted in localStorage and toggled from the button
 * next to the sound speaker in the player header — same idiom as
 * player-sound.js, deliberately, so the header has one row of small
 * personal switches rather than two unrelated affordances.
 *
 * The state lives as the class `a11y` on <html>. The CSS that reacts to it
 * is www/css/src/09-a11y.css.
 *
 * NOTE: the storage key is duplicated in a two-line inline script in
 * player.html's <head>. That is intentional and the duplication is the
 * point: this bundle loads at the end of <body>, so if the class were only
 * applied from here the page would paint once at normal size and then jump.
 * If you rename A11Y_KEY, rename it there too — the test
 * tests/test_accessibility_mode_372.py pins the two together so a one-sided
 * rename fails CI rather than silently reintroducing the flash.
 */
(function () {
    'use strict';

    var A11Y_KEY = 'quizify_a11y';

    var enabled = false;
    try {
        enabled = window.localStorage.getItem(A11Y_KEY) === '1';
    } catch (e) {
        // Private mode / storage disabled: the mode still works for this
        // page view, it just does not survive a reload.
        enabled = false;
    }

    function apply() {
        var root = document.documentElement;
        if (!root) return;
        // No classList.toggle(name, force) — that second argument is absent
        // on older WebKit, which is exactly the vintage of phone likely to
        // be handed to a guest.
        if (enabled) root.classList.add('a11y');
        else root.classList.remove('a11y');
    }

    function isEnabled() {
        return enabled;
    }

    function toggle() {
        enabled = !enabled;
        try {
            window.localStorage.setItem(A11Y_KEY, enabled ? '1' : '0');
        } catch (e) { /* ignore — see above */ }
        apply();
        return enabled;
    }

    // Re-assert on load: the head script already set the class, but a
    // reload that races storage (or a browser that blocked it there) would
    // otherwise leave the class and the preference out of step.
    apply();

    window.QuizifyA11y = {
        isEnabled: isEnabled,
        toggle: toggle,
        STORAGE_KEY: A11Y_KEY
    };
})();
