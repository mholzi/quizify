/**
 * Quizify - Shared Utilities
 * Minimal shared helpers used by admin.js, dashboard.html, and analytics.html.
 * Player modules use player-utils.js instead.
 */
(function () {
    'use strict';

    // Single source of truth for the player-name limit. Both the player
    // join flow (via player-utils.js) and the admin self-join modal
    // (admin.js) validate against this — keep the two in sync by routing
    // through validateName() below, never re-implementing the rule.
    var MAX_NAME_LENGTH = 20;

    function escapeHtml(str) {
        if (str == null) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // Localised name validation shared across player + admin. Error
    // messages resolve through QuizifyI18n when ready (so a German player
    // sees German), with an English fallback if i18n hasn't loaded yet.
    function validateName(name) {
        var i18n = window.QuizifyI18n;
        var ready = i18n && i18n.isReady && i18n.isReady();
        var trimmed = (name || '').trim();
        if (!trimmed) {
            return {
                valid: false,
                error: ready ? i18n.t('errors.NAME_INVALID') : 'Please enter a name'
            };
        }
        if (trimmed.length > MAX_NAME_LENGTH) {
            return {
                valid: false,
                error: ready
                    ? i18n.t('errors.NAME_TOO_LONG', { max: MAX_NAME_LENGTH })
                    : 'Name too long (max ' + MAX_NAME_LENGTH + ' characters)'
            };
        }
        return { valid: true, name: trimmed };
    }

    // ---- Question image URLs -------------------------------------------
    //
    // Mirror of the server's _sanitize_image_url (game/questions.py). Two
    // forms are allowed: an absolute http(s) URL, and a path under the
    // integration's own static mount (#536). Everything else — any other
    // relative path, javascript:/data:, a non-string — renders text-only.
    //
    // The server already sanitises; this stays as defence-in-depth for a
    // payload that never went through it. It lives here, in one place, so
    // the rule can't drift per view again: the dashboard, the player and
    // the lightning round each carried their own copy of an http(s)-only
    // test, which silently dropped every image the packs from #537 ship
    // (#540).
    var LOCAL_IMAGE_PREFIX = '/quizify/static/';
    var TRAVERSAL_MARKERS = ['..', '%2e%2e'];

    function safeImageUrl(url) {
        if (typeof url !== 'string') return '';
        var trimmed = url.trim();
        if (!trimmed) return '';
        if (/^https?:\/\//i.test(trimmed)) return trimmed;
        if (trimmed.indexOf(LOCAL_IMAGE_PREFIX) === 0) {
            var lowered = trimmed.toLowerCase();
            for (var i = 0; i < TRAVERSAL_MARKERS.length; i++) {
                if (lowered.indexOf(TRAVERSAL_MARKERS[i]) !== -1) return '';
            }
            return trimmed;
        }
        return '';
    }

    // ---- Admin session token -------------------------------------------
    //
    // The admin session token is a PERSISTENT credential, and every reader
    // must go through the two helpers below so the storage choice lives in
    // exactly one place.
    //
    // It used to be kept in sessionStorage, which dies with the tab. The
    // server, meanwhile, persists its copy to disk and only ever bootstraps
    // a NEW token when no token exists at all (connection.py
    // try_bootstrap_admin). Closing the tab — or restarting HA — therefore
    // orphaned the credential: no client could present it, and no client
    // could earn a replacement. Every fresh admin tab was refused with
    // "Admin only", so the admin-connect frame never arrived and the setup
    // panel's TTS/light/media_player dropdowns stayed empty (the symptom
    // that surfaced this, 2026-08-05). localStorage gives the token the
    // same lifetime the server already assumes it has.
    var ADMIN_TOKEN_KEY = 'quizify_admin_session_token';

    function writeAdminToken(token) {
        if (!token) return;
        try {
            localStorage.setItem(ADMIN_TOKEN_KEY, token);
        } catch (e) { /* storage disabled/full — the socket still works */ }
    }

    function readAdminToken() {
        try {
            var tok = localStorage.getItem(ADMIN_TOKEN_KEY);
            if (tok) return tok;
            // Migration: a tab opened before this change still holds the
            // old per-tab token. Promote it to the durable store on first
            // read so that tab's session survives its own closing.
            var legacy = sessionStorage.getItem(ADMIN_TOKEN_KEY);
            if (legacy) {
                writeAdminToken(legacy);
                return legacy;
            }
        } catch (e) { /* storage disabled — fall through to null */ }
        return null;
    }

    window.QuizifyUtils = {
        escapeHtml: escapeHtml,
        safeImageUrl: safeImageUrl,
        validateName: validateName,
        MAX_NAME_LENGTH: MAX_NAME_LENGTH,
        readAdminToken: readAdminToken,
        writeAdminToken: writeAdminToken,
        ADMIN_TOKEN_KEY: ADMIN_TOKEN_KEY,
    };
})();
