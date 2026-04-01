/**
 * Quizify - Shared Utilities
 * Minimal shared helpers used by admin.js, dashboard.html, and analytics.html.
 * Player modules use player-utils.js instead.
 */
(function () {
    'use strict';

    function escapeHtml(str) {
        if (str == null) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    window.QuizifyUtils = {
        escapeHtml: escapeHtml,
    };
})();
