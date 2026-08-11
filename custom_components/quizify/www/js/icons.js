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
        worldcup: '<path d="M7 4h10v4a5 5 0 0 1-10 0V4z"/><path d="M7 6H4v1a3.5 3.5 0 0 0 3.5 3.5M17 6h3v1a3.5 3.5 0 0 1-3.5 3.5"/><path d="M12 13v4M9 20h6M10 20a2 2 0 0 1 2-2 2 2 0 0 1 2 2"/>',
        // The packs that don't fit a subject theme — picture rounds and
        // estimation (#537, #275). Without an entry they fell through to
        // ``mixed``, so "Bilderrätsel", "Schätzfragen" and "Gemischt" wore
        // the same glyph, and "Mixed" means something else entirely (every
        // pack at once). A framed picture reads for both: the estimation
        // packs are the other kind of "look at this and judge".
        trivia: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle class="d" cx="8.5" cy="10" r="1.3"/><path d="M4 17l4.5-4.5 3 3L15 11l5 5"/>'
    };

    // UI line icons keyed by a semantic name (NOT a theme). Used for the
    // standalone emoji-as-icon surfaces missed by the original #212 P1
    // inventory: admin lobby (cast/join/start), player nav + section
    // icons, the game control bar, and status/utility glyphs (#225 P4).
    // Same Rounded Duotone style as CATEGORY_ICON_SVG — `.d` marks a
    // filled detail dot (fill:currentColor in CSS). Paths approved by
    // Markus (2026-06-10) — do not redraw.
    var UI_ICON_SVG = {
        tv: '<rect x="3" y="5" width="18" height="11" rx="2"/><path d="M8.5 20h7M12 16v4"/>',
        controller: '<path d="M9 9h6M16.5 9a5 5 0 0 1 4.4 7.3l-.6 1.1a2.2 2.2 0 0 1-3.8.2L15 15H9l-1.5 2.6a2.2 2.2 0 0 1-3.8-.2l-.6-1.1A5 5 0 0 1 7.5 9z"/><path d="M6 12.5h2.4M7.2 11.3v2.4"/><circle class="d" cx="16.3" cy="11.8" r="1"/><circle class="d" cx="18" cy="13.3" r="1"/>',
        play: '<path d="M8 5.5v13l10.5-6.5z"/>',
        target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.8"/><circle class="d" cx="12" cy="12" r="1.1"/>',
        brain: '<path d="M12 5.5V19"/><path d="M12 6a3 3 0 0 0-5.3-1.9A2.7 2.7 0 0 0 4.4 8 2.8 2.8 0 0 0 5 13.4 2.9 2.9 0 0 0 8.5 18 3 3 0 0 0 12 18.5"/><path d="M12 6a3 3 0 0 1 5.3-1.9A2.7 2.7 0 0 1 19.6 8 2.8 2.8 0 0 1 19 13.4 2.9 2.9 0 0 1 15.5 18 3 3 0 0 1 12 18.5"/>',
        trophy: '<path d="M8 4h8v4a4 4 0 0 1-8 0z"/><path d="M8 6H5v1a3 3 0 0 0 3 3M16 6h3v1a3 3 0 0 1-3 3"/><path d="M12 12v4M9 20h6M9.5 20a2.5 2.5 0 0 1 5 0"/>',
        party: '<path d="M4 20l4.5-12 7.5 7.5z"/><path d="M8 12l-1 4M11 9l4 4"/><path d="M15 4.5l.6 1.6M19 6l-1.4 1.2M19.5 10.5l-1.7-.3"/>',
        hourglass: '<path d="M6.5 3.5h11M6.5 20.5h11"/><path d="M7.5 3.5c0 5 4.5 5.5 4.5 8.5s-4.5 3.5-4.5 8.5M16.5 3.5c0 5-4.5 5.5-4.5 8.5s4.5 3.5 4.5 8.5"/>',
        sparkle: '<path d="M12 3.5l1.9 6.6L20 12l-6.1 1.9L12 20.5l-1.9-6.6L4 12l6.1-1.9z"/>',
        bulb: '<path d="M9.5 18.5h5M11 21h2"/><path d="M12 3a6 6 0 0 0-3.8 10.6c.8.7 1.3 1.5 1.3 2.4h5c0-.9.5-1.7 1.3-2.4A6 6 0 0 0 12 3z"/>',
        signaloff: '<path d="M5 12.6a10 10 0 0 1 5-2.7M14 9.9a10 10 0 0 1 5 2.7"/><path d="M8 15.6a5.5 5.5 0 0 1 3-1.4M13 14.2a5.5 5.5 0 0 1 3 1.4"/><circle class="d" cx="12" cy="19" r="1.1"/><path d="M4 4l16 16"/>',
        copy: '<rect x="8.5" y="8.5" width="11" height="12.5" rx="2"/><path d="M5.5 15.5V5a2 2 0 0 1 2-2H15"/>',
        bolt: '<path d="M13 3l-7.5 10H11l-1 8 8-11h-5.5z"/>',
        finish: '<path d="M5.5 3.5v17"/><path d="M5.5 4.5h12l-2.5 3.5 2.5 3.5h-12"/><path d="M9.5 4.5v7M13.5 4.5v7M5.5 8h12"/>',
        pause: '<path d="M9 5v14M15 5v14"/>',
        stop: '<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
        skipnext: '<path d="M6.5 5.5v13l8-6.5zM16.5 5.5v13"/>',
        skipprev: '<path d="M17.5 5.5v13l-8-6.5zM7.5 5.5v13"/>',
        // Setup-preset + end-game-award glyphs (#219 P2). Approved by
        // Markus (2026-06-10) — do not redraw.
        gear: '<circle cx="12" cy="12" r="3"/><path d="M12 4.5V2.5M12 21.5v-2M4.5 12h-2M21.5 12h-2M6.5 6.5 5 5M19 19l-1.5-1.5M17.5 6.5 19 5M5 19l1.5-1.5"/>',
        medal: '<circle cx="12" cy="14.5" r="5.5"/><path d="M9.2 9.7 6.5 3.2h3.2L12 7.5 14.3 3.2h3.2L14.8 9.7"/><path d="M12 12.2l.95 1.93 2.13.31-1.54 1.5.36 2.12L12 16.6l-1.9 1 .36-2.12-1.54-1.5 2.13-.31z" class="d"/>',
        rocket: '<path d="M12 3c2.4 1.7 3.7 4.6 3.7 7.8 0 1.6-.4 3-1 4.2H9.3c-.6-1.2-1-2.6-1-4.2C8.3 7.6 9.6 4.7 12 3z"/><circle class="d" cx="12" cy="9.5" r="1.1"/><path d="M9 15.5l-2.2 2.2.3 2.6 2.4-1.2M15 15.5l2.2 2.2-.3 2.6-2.4-1.2"/>',
        flame: '<path d="M12 21a5.5 5.5 0 0 0 5.5-5.5c0-3.6-3-5.4-3-8.6-2 1-2.6 3-2.6 4.6-1-.5-1.6-2-1.6-3.6-2.6 2-4.3 4.6-4.3 7.6A5.5 5.5 0 0 0 12 21z"/>',
        freeze: '<path d="M12 2.5v19M3.8 7.25l16.4 9.5M20.2 7.25l-16.4 9.5"/><path d="M12 5.6l-2 1.2M12 5.6l2 1.2M12 18.4l-2-1.2M12 18.4l2-1.2M5.6 9.2v2.3M18.4 9.2v2.3M5.6 12.5v2.3M18.4 12.5v2.3"/>',
        // Highlights tab — reuse the popculture clapperboard glyph.
        highlights: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9.5h18"/><path d="M7.5 5v4.5M12 5v4.5M16.5 5v4.5"/>',
        // Reveal-feedback + toast glyphs (#220 P3): emoji pulled out of the
        // translated strings and rendered as SVG beside the text. Approved by
        // Markus (2026-06-10) — do not redraw. heartbreak's crack is a PLAIN
        // stroked path (no .d) so it renders as a stroke, matching the preview.
        check:      '<path d="M4.5 12.5l4.5 4.5L19.5 6.5"/>',
        cross:      '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
        heartbreak: '<path d="M12 20.3C6.6 16 4 13.4 4 10.1 4 7.8 5.8 6 8 6c1.3 0 2.5.6 3.3 1.6L12 8.4M12 20.3C17.4 16 20 13.4 20 10.1 20 7.8 18.2 6 16 6c-1.3 0-2.5.6-3.3 1.6L12 8.4"/><path d="M12 8.4l-1.6 3.1 2.6 1.2-1.6 3.1"/>',
        steal:      '<path d="M3.5 10.2c2.2-1 5.2-1.6 8.5-1.6s6.3.6 8.5 1.6c-.2 3.1-1.9 5-4.8 5-1.6 0-2.5-.8-3.2-1.7h-1c-.7.9-1.6 1.7-3.2 1.7-2.9 0-4.6-1.9-4.8-5z"/><circle class="d" cx="8.3" cy="11" r="1.05"/><circle class="d" cx="15.7" cy="11" r="1.05"/>',
        joker:      '<rect x="6.5" y="3.5" width="11" height="17" rx="2.2"/><path d="M12 7.6l1.05 2.13 2.35.34-1.7 1.66.4 2.34L12 13.27l-2.1 1.1.4-2.34-1.7-1.66 2.35-.34z" class="d"/>'
    };

    // Accent tint per theme, cycling the 4 Soft Parlor accents; mixed stays
    // neutral. Consumed for the duotone backing disc (see .qz-icon CSS).
    var CATEGORY_TINT = {
        mixed: 'mix', geography: 'coral', nature: 'sage', popculture: 'sky',
        sport: 'sun', music: 'coral', science: 'sage', history: 'sky',
        food: 'sun', tech: 'coral', worldcup: 'sun', trivia: 'sage'
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

    // Returns a full <svg> element string for a UI icon name (#225 P4),
    // or '' when the name is unknown (so a bad data-ui-icon paints
    // nothing rather than a misleading fallback glyph).
    function uiIcon(name) {
        var glyph = UI_ICON_SVG[name];
        if (!glyph) return '';
        return '<svg viewBox="0 0 24 24" aria-hidden="true">' + glyph + '</svg>';
    }

    window.QuizifyIcons = {
        CATEGORY_ICON_SVG: CATEGORY_ICON_SVG,
        CATEGORY_TINT: CATEGORY_TINT,
        UI_ICON_SVG: UI_ICON_SVG,
        inner: inner,
        icon: icon,
        tint: tint,
        uiIcon: uiIcon
    };
})();
