/**
 * Quizify — shared SVG line-icon helper (Soft Parlor).
 *
 * Single source of truth for the category/theme line icons introduced for
 * the welcome/setup hero in #211. Both admin.js and the player JS consume
 * this so emoji-as-icon can be replaced with themeable SVG across the app
 * (issue #212). The app serves JS un-bundled, so this exposes a plain
 * global (window.QuizifyIcons) — NOT an ES module.
 *
 * Styling is Option 2 "Rounded Duotone" (issue #212): 2px rounded strokes
 * over a soft accent-tinted backing disc. Color comes from the surrounding
 * context via currentColor; tints are flat (DESIGN.md — no gradients).
 */
(function () {
    'use strict';

    // SVG line icons keyed by a category's data-theme. Keyed by theme so
    // both languages (Geographie / Geography) share one glyph. `.d` marks
    // a filled detail dot (rendered with fill:currentColor in CSS).
    var CATEGORY_ICON_SVG = {
        mixed: '<rect x="4" y="4" width="16" height="16" rx="4"/><circle class="d" cx="9" cy="9" r="1.1"/><circle class="d" cx="15" cy="9" r="1.1"/><circle class="d" cx="12" cy="12" r="1.1"/><circle class="d" cx="9" cy="15" r="1.1"/><circle class="d" cx="15" cy="15" r="1.1"/>',
        geography: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c3.2 2.6 3.2 15.4 0 18M12 3c-3.2 2.6-3.2 15.4 0 18"/>',
        nature: '<path d="M5 19c0-9 7-14 15-14 0 9-7 14-15 14z"/><path d="M5 19c4.5-4.5 8-7 11-8"/>',
        popculture: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9.5h18"/><path d="M7.5 5v4.5M12 5v4.5M16.5 5v4.5"/>',
        sport: '<path d="M6 21V4"/><path d="M6 4h11l-2.5 3.5L17 11H6"/>',
        music: '<path d="M9 17V5l10-2v12"/><circle cx="6.5" cy="17" r="2.5"/><circle cx="16.5" cy="15" r="2.5"/>',
        science: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3"/><path d="M8 15h8"/>',
        history: '<path d="M7 4h9a2 2 0 0 1 2 2v12a2 2 0 0 0 2 2H8a2 2 0 0 1-2-2V6"/><path d="M6 6a2 2 0 0 0-2 2v1h2"/><path d="M10 9h5M10 13h5"/>',
        food: '<path d="M7 3v18M5 3v6a2 2 0 0 0 4 0V3"/><path d="M16 3c-1.6 0-2.5 2.2-2.5 5s1 4 2.5 4 2.5-1.2 2.5-4-.9-5-2.5-5zM16 12v9"/>',
        tech: '<path d="M9.5 18h5M11 21h2"/><path d="M12 3a6 6 0 0 0-3.8 10.6c.8.7 1.3 1.5 1.3 2.4h5c0-.9.5-1.7 1.3-2.4A6 6 0 0 0 12 3z"/>',
        worldcup: '<path d="M7 4h10v4a5 5 0 0 1-10 0V4z"/><path d="M7 6H4v1a3.5 3.5 0 0 0 3.5 3.5M17 6h3v1a3.5 3.5 0 0 1-3.5 3.5"/><path d="M12 13v4M9 20h6M10 20a2 2 0 0 1 2-2 2 2 0 0 1 2 2"/>'
    };

    // Accent tint per theme, cycling the 4 Soft Parlor accents; mixed stays
    // neutral. Consumed for the duotone backing disc (see .qz-icon CSS).
    var CATEGORY_TINT = {
        mixed: 'mix', geography: 'coral', nature: 'sage', popculture: 'sky',
        sport: 'sun', music: 'coral', science: 'sage', history: 'sky',
        food: 'sun', tech: 'coral', worldcup: 'sun'
    };

    // Returns the inner SVG markup (paths only) for a theme, or the mixed
    // fallback. Use when you need to inject into an existing <svg>.
    function inner(theme) {
        return CATEGORY_ICON_SVG[theme] || CATEGORY_ICON_SVG.mixed;
    }

    // Returns a full <svg> element string for a theme.
    function icon(theme) {
        return '<svg viewBox="0 0 24 24" aria-hidden="true">' + inner(theme) + '</svg>';
    }

    // Returns the tint key (mix/coral/sage/sky/sun) for a theme.
    function tint(theme) {
        return CATEGORY_TINT[theme] || 'mix';
    }

    window.QuizifyIcons = {
        CATEGORY_ICON_SVG: CATEGORY_ICON_SVG,
        CATEGORY_TINT: CATEGORY_TINT,
        inner: inner,
        icon: icon,
        tint: tint
    };
})();
