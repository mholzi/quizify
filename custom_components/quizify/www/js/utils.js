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

    window.QuizifyUtils = {
        escapeHtml: escapeHtml,
        validateName: validateName,
        MAX_NAME_LENGTH: MAX_NAME_LENGTH,
    };
})();
