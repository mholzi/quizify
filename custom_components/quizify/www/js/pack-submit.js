/**
 * Quizify community-pack submission (#180).
 *
 * Lets a host paste/compose a community question pack, validates it in the
 * browser against the #179 schema (per-field ✓/✗ table), and — only when the
 * integration has a worker URL configured — submits it. The submit goes to
 * Quizify's own endpoint, which proxies to the worker that holds the GitHub
 * token; the browser never talks to GitHub directly.
 *
 * The whole section stays hidden until /api/quizify/pack-submit/config reports
 * enabled:true. Error codes (INVALID_FORMAT / RATE_LIMITED / GITHUB_ERROR /
 * SUBMIT_DISABLED) map to i18n keys with a fallback to the raw message.
 *
 * Self-contained IIFE on window.QuizifyPackSubmit — no coupling to admin.js.
 */
window.QuizifyPackSubmit = (function () {
    'use strict';

    var CONFIG_URL = '/api/quizify/pack-submit/config';
    var SUBMIT_URL = '/api/quizify/pack-submit';
    var SUBMISSIONS_URL = '/api/quizify/pack-submit/submissions';

    // Defaults; overwritten by the server's reported limits.
    var limits = {
        max_questions: 500,
        min_questions: 1,
        answers_per_question: 3,
        max_bytes: 1048576
    };

    function t(key, params) {
        if (window.QuizifyI18n && typeof window.QuizifyI18n.t === 'function') {
            return window.QuizifyI18n.t(key, params);
        }
        return key;
    }

    function el(id) { return document.getElementById(id); }

    /**
     * Validate a parsed pack object against the #179 schema.
     * Returns { ok, fields: [{label, ok, detail}] } — fields drives the ✓/✗ table.
     */
    function validatePack(pack) {
        var fields = [];
        function add(label, ok, detail) { fields.push({ label: label, ok: ok, detail: detail || '' }); }

        if (typeof pack !== 'object' || pack === null || Array.isArray(pack)) {
            add(t('packSubmit.field.object'), false, t('packSubmit.err.notObject'));
            return { ok: false, fields: fields };
        }

        var nameOk = typeof pack.name === 'string' && pack.name.trim().length > 0;
        add(t('packSubmit.field.name'), nameOk);

        var langOk = typeof pack.language === 'string' && pack.language.trim().length > 0;
        add(t('packSubmit.field.language'), langOk);

        var qs = pack.questions;
        var qsIsList = Array.isArray(qs) && qs.length > 0;
        if (!qsIsList) {
            add(t('packSubmit.field.questions'), false, t('packSubmit.err.questionsEmpty'));
            return { ok: false, fields: fields };
        }
        var countOk = qs.length >= limits.min_questions && qs.length <= limits.max_questions;
        add(t('packSubmit.field.questions'), countOk,
            t('packSubmit.field.questionCount', { count: qs.length }));

        var allQuestionsOk = true;
        var seen = {};
        var problems = [];
        for (var i = 0; i < qs.length; i++) {
            var q = qs[i];
            var n = i + 1;
            if (typeof q !== 'object' || q === null) {
                allQuestionsOk = false; problems.push('Q' + n + ': ' + t('packSubmit.err.notObject')); continue;
            }
            if (typeof q.id !== 'string' || !q.id.trim()) {
                allQuestionsOk = false; problems.push('Q' + n + ': id');
            } else if (seen[q.id]) {
                allQuestionsOk = false; problems.push('Q' + n + ': dup id ' + q.id);
            } else { seen[q.id] = true; }
            if (typeof q.question !== 'string' || !q.question.trim()) {
                allQuestionsOk = false; problems.push('Q' + n + ': question');
            }
            var ans = q.answers;
            if (!Array.isArray(ans) || ans.length !== limits.answers_per_question) {
                allQuestionsOk = false; problems.push('Q' + n + ': answers');
                continue;
            }
            var correct = 0;
            for (var j = 0; j < ans.length; j++) {
                var a = ans[j];
                if (typeof a !== 'object' || a === null || typeof a.text !== 'string' || !a.text.trim()) {
                    allQuestionsOk = false; problems.push('Q' + n + ': answer text');
                }
                if (a && a.correct === true) { correct++; }
            }
            if (correct !== 1) {
                allQuestionsOk = false; problems.push('Q' + n + ': one correct');
            }
        }
        add(t('packSubmit.field.questionShape'), allQuestionsOk, problems.slice(0, 3).join('; '));

        var allOk = fields.every(function (f) { return f.ok; });
        return { ok: allOk, fields: fields };
    }

    function renderResult(parsed) {
        var resultEl = el('pack-submit-result');
        var submitBtn = el('pack-submit-btn');
        if (!resultEl) { return false; }

        if (parsed === null) {
            resultEl.innerHTML = '<div class="pack-submit-row pack-submit-row--bad">' +
                '<span class="pack-submit-mark">✗</span>' +
                '<span>' + t('packSubmit.err.json') + '</span></div>';
            if (submitBtn) { submitBtn.disabled = true; }
            return false;
        }

        var res = validatePack(parsed);
        var html = '';
        res.fields.forEach(function (f) {
            html += '<div class="pack-submit-row ' +
                (f.ok ? 'pack-submit-row--ok' : 'pack-submit-row--bad') + '">' +
                '<span class="pack-submit-mark">' + (f.ok ? '✓' : '✗') + '</span>' +
                '<span class="pack-submit-label">' + f.label + '</span>' +
                (f.detail ? '<span class="pack-submit-detail">' + f.detail + '</span>' : '') +
                '</div>';
        });
        resultEl.innerHTML = html;
        if (submitBtn) { submitBtn.disabled = !res.ok; }
        return res.ok;
    }

    function parseInput() {
        var input = el('pack-submit-input');
        if (!input || !input.value.trim()) { return undefined; }
        try { return JSON.parse(input.value); } catch (_e) { return null; }
    }

    function setStatus(msg, kind) {
        var statusEl = el('pack-submit-status');
        if (!statusEl) { return; }
        statusEl.textContent = msg || '';
        statusEl.className = 'pack-submit-status' + (kind ? ' pack-submit-status--' + kind : '');
    }

    function errorText(payload, fallbackMsg) {
        var code = payload && payload.code;
        if (code) {
            var translated = t('errors.' + code);
            if (translated && translated !== 'errors.' + code) { return translated; }
        }
        return (payload && payload.message) || fallbackMsg || t('errors.UNKNOWN');
    }

    async function doSubmit() {
        var parsed = parseInput();
        if (!parsed) { renderResult(parsed === undefined ? null : parsed); return; }
        if (!renderResult(parsed)) { return; }

        var submitBtn = el('pack-submit-btn');
        if (submitBtn) { submitBtn.disabled = true; }
        setStatus(t('packSubmit.submitting'), 'pending');

        try {
            var resp = await fetch(SUBMIT_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pack: parsed })
            });
            var data = {};
            try { data = await resp.json(); } catch (_e) { /* ignore */ }
            if (!resp.ok || !data.ok) {
                setStatus(errorText(data, t('packSubmit.failed')), 'bad');
                if (submitBtn) { submitBtn.disabled = false; }
                return;
            }
            var link = data.issue_url
                ? ' (#' + (data.issue_number || '?') + ')'
                : '';
            setStatus(t('packSubmit.success') + link, 'ok');
            var input = el('pack-submit-input');
            if (input) { input.value = ''; }
            loadSubmissions();
        } catch (err) {
            setStatus(errorText({ code: 'GITHUB_ERROR' }, String(err)), 'bad');
            if (submitBtn) { submitBtn.disabled = false; }
        }
    }

    function statusLabel(status) {
        var key = 'packSubmit.status.' + (status || 'pending');
        var translated = t(key);
        return translated === key ? (status || 'pending') : translated;
    }

    async function loadSubmissions() {
        var listEl = el('pack-submit-list');
        if (!listEl) { return; }
        try {
            var resp = await fetch(SUBMISSIONS_URL);
            if (!resp.ok) { return; }
            var data = await resp.json();
            var subs = (data && data.submissions) || [];
            if (!subs.length) { listEl.innerHTML = ''; return; }
            var html = '<div class="pack-submit-list-title">' + t('packSubmit.recent') + '</div>';
            subs.slice(0, 10).forEach(function (s) {
                var label = (s.issue_url ? '<a href="' + s.issue_url + '" target="_blank" rel="noopener">' +
                    (s.name || '?') + '</a>' : (s.name || '?'));
                html += '<div class="pack-submit-list-row">' +
                    '<span class="pack-submit-list-name">' + label + '</span>' +
                    '<span class="pack-submit-badge pack-submit-badge--' + (s.status || 'pending') + '">' +
                    statusLabel(s.status) + '</span></div>';
            });
            listEl.innerHTML = html;
        } catch (_e) { /* best-effort */ }
    }

    async function init() {
        var section = el('pack-submit-section');
        if (!section) { return; }
        try {
            var resp = await fetch(CONFIG_URL);
            if (!resp.ok) { return; }
            var cfg = await resp.json();
            if (!cfg || !cfg.enabled) { return; }  // stays hidden — feature off
            if (cfg.limits) { limits = Object.assign(limits, cfg.limits); }
        } catch (_e) {
            return;  // can't reach config → leave hidden
        }

        section.classList.remove('hidden');
        if (window.QuizifyI18n && window.QuizifyI18n.initPageTranslations) {
            window.QuizifyI18n.initPageTranslations(section);
        }

        var input = el('pack-submit-input');
        if (input) {
            input.addEventListener('input', function () {
                var parsed = parseInput();
                // undefined (empty) and null (bad JSON) both render the JSON-error
                // hint; an object renders the per-field ✓/✗ table.
                renderResult(parsed === undefined ? null : parsed);
            });
        }
        var submitBtn = el('pack-submit-btn');
        if (submitBtn) { submitBtn.addEventListener('click', doSubmit); }

        loadSubmissions();
    }

    return {
        init: init,
        validatePack: validatePack  // exported for tests
    };
})();

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { window.QuizifyPackSubmit.init(); });
} else {
    window.QuizifyPackSubmit.init();
}
