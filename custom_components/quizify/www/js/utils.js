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
        validateName: validateName,
        MAX_NAME_LENGTH: MAX_NAME_LENGTH,
        readAdminToken: readAdminToken,
        writeAdminToken: writeAdminToken,
        ADMIN_TOKEN_KEY: ADMIN_TOKEN_KEY,
    };
})();
