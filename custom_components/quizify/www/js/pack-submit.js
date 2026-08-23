/**
 * Quizify community-pack submission (#180) — Variant C "Stepper".
 *
 * Lets a host paste/compose a community question pack and walk a guided
 * 3-step wizard: Compose → Check → Submit. A progress rail tracks the active
 * step; the Check step shows grouped ✓/✗ results (pack structure + per-question
 * checks). The submit goes to Quizify's own endpoint, which proxies to the
 * worker that holds the GitHub token; the browser never talks to GitHub
 * directly. "My submissions" renders as a status timeline.
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
    var REQUEST_URL = '/api/quizify/pack-submit/request';
    var SUBMISSIONS_URL = '/api/quizify/pack-submit/submissions';

    // Defaults; overwritten by the server's reported limits.
    var limits = {
        max_questions: 500,
        min_questions: 1,
        answers_per_question: 3,
        max_bytes: 1048576,
        max_theme_chars: 80,
        max_notes_chars: 500
    };

    // The pack that last passed validation — carried from Check to Submit.
    var validatedPack = null;

    function t(key, params) {
        if (window.QuizifyI18n && typeof window.QuizifyI18n.t === 'function') {
            return window.QuizifyI18n.t(key, params);
        }
        return key;
    }

    function el(id) { return document.getElementById(id); }

    function escapeHtml(str) {
        return String(str).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    /**
     * Validate a parsed pack object against the #179 schema.
     *
     * Returns { ok, fields, groups }:
     *   fields — flat [{label, ok, detail}] (kept for tests / back-compat).
     *   groups — [{ key, title, ok, pass, total, rows:[{label, ok, detail}] }]
     *            drives the grouped Stepper "Check" view: one "Pack structure"
     *            group and one "Per-question checks" group.
     */
    function validatePack(pack) {
        var fields = [];

        // structure group rows
        var structRows = [];
        function struct(label, ok, detail) {
            var row = { label: label, ok: ok, detail: detail || '' };
            structRows.push(row);
            fields.push(row);
        }
        // per-question group rows
        var qRows = [];
        function qcheck(label, ok, detail) {
            var row = { label: label, ok: ok, detail: detail || '' };
            qRows.push(row);
            fields.push(row);
        }

        function buildGroups() {
            function grp(key, titleKey, rows) {
                var pass = rows.filter(function (r) { return r.ok; }).length;
                return {
                    key: key,
                    title: t(titleKey),
                    ok: rows.length > 0 && pass === rows.length,
                    pass: pass,
                    total: rows.length,
                    rows: rows
                };
            }
            var groups = [grp('structure', 'packSubmit.group.structure', structRows)];
            if (qRows.length) {
                groups.push(grp('questions', 'packSubmit.group.questions', qRows));
            }
            return groups;
        }

        function result() {
            var allOk = fields.length > 0 && fields.every(function (f) { return f.ok; });
            return { ok: allOk, fields: fields, groups: buildGroups() };
        }

        if (typeof pack !== 'object' || pack === null || Array.isArray(pack)) {
            struct(t('packSubmit.field.object'), false, t('packSubmit.err.notObject'));
            return result();
        }

        struct(t('packSubmit.field.object'), true);

        var nameOk = typeof pack.name === 'string' && pack.name.trim().length > 0;
        struct(t('packSubmit.field.name'), nameOk);

        var langOk = typeof pack.language === 'string' && pack.language.trim().length > 0;
        struct(t('packSubmit.field.language'), langOk,
            langOk ? pack.language.trim() : '');

        var qs = pack.questions;
        var qsIsList = Array.isArray(qs) && qs.length > 0;
        if (!qsIsList) {
            struct(t('packSubmit.field.questions'), false, t('packSubmit.err.questionsEmpty'));
            return result();
        }
        var countOk = qs.length >= limits.min_questions && qs.length <= limits.max_questions;
        struct(t('packSubmit.field.questions'), countOk, String(qs.length));

        // ---- per-question checks (two aggregate rows: unique ids; answers shape)
        var uniqueOk = true;
        var shapeOk = true;
        var dupDetail = '';
        var shapeDetail = '';
        var seen = {};
        for (var i = 0; i < qs.length; i++) {
            var q = qs[i];
            var n = i + 1;
            if (typeof q !== 'object' || q === null) {
                shapeOk = false; if (!shapeDetail) { shapeDetail = 'Q' + n + ': ' + t('packSubmit.err.notObject'); }
                continue;
            }
            if (typeof q.id !== 'string' || !q.id.trim()) {
                uniqueOk = false; if (!dupDetail) { dupDetail = 'Q' + n + ': id'; }
            } else if (seen[q.id]) {
                uniqueOk = false; if (!dupDetail) { dupDetail = t('packSubmit.err.dupId', { id: q.id }); }
            } else { seen[q.id] = true; }
            if (typeof q.question !== 'string' || !q.question.trim()) {
                shapeOk = false; if (!shapeDetail) { shapeDetail = 'Q' + n + ': question'; }
            }
            var ans = q.answers;
            if (!Array.isArray(ans) || ans.length !== limits.answers_per_question) {
                shapeOk = false; if (!shapeDetail) { shapeDetail = 'Q' + n + ': answers'; }
                continue;
            }
            var correct = 0;
            for (var j = 0; j < ans.length; j++) {
                var a = ans[j];
                if (typeof a !== 'object' || a === null || typeof a.text !== 'string' || !a.text.trim()) {
                    shapeOk = false; if (!shapeDetail) { shapeDetail = 'Q' + n + ': answer text'; }
                }
                if (a && a.correct === true) { correct++; }
            }
            if (correct !== 1) {
                shapeOk = false; if (!shapeDetail) { shapeDetail = 'Q' + n + ': one correct'; }
            }
        }
        qcheck(t('packSubmit.field.uniqueIds'), uniqueOk, dupDetail);
        qcheck(t('packSubmit.field.answersShape', { n: limits.answers_per_question }),
            shapeOk, shapeDetail);

        return result();
    }

    /** Move the wizard to step 1/2/3 — updates rail state and pane visibility. */
    function goToStep(step) {
        var section = el('pack-submit-section');
        if (!section) { return; }
        section.querySelectorAll('.pack-step').forEach(function (s) {
            var n = parseInt(s.getAttribute('data-step'), 10);
            s.classList.remove('done', 'active', 'todo');
            if (n < step) { s.classList.add('done'); s.querySelector('.pack-step-n').textContent = '✓'; }
            else if (n === step) { s.classList.add('active'); s.querySelector('.pack-step-n').textContent = String(n); }
            else { s.classList.add('todo'); s.querySelector('.pack-step-n').textContent = String(n); }
        });
        section.querySelectorAll('.pack-step-bar').forEach(function (b) {
            var n = parseInt(b.getAttribute('data-bar'), 10);
            b.classList.toggle('fill', n < step);
        });
        section.querySelectorAll('.pack-step-pane').forEach(function (p) {
            var n = parseInt(p.getAttribute('data-pane'), 10);
            p.classList.toggle('hidden', n !== step);
        });
    }

    /** Render grouped ✓/✗ results into the Check pane. Returns res.ok. */
    function renderResult(parsed) {
        var resultEl = el('pack-submit-result');
        var continueBtn = el('pack-submit-continue-btn');
        if (!resultEl) { return false; }

        if (parsed === null) {
            resultEl.innerHTML = '<div class="pack-vgroup">' +
                '<div class="pack-vg-head bad"><span class="pack-vg-ic">✕</span>' +
                '<span>' + escapeHtml(t('packSubmit.err.json')) + '</span></div></div>';
            if (continueBtn) { continueBtn.disabled = true; }
            return false;
        }

        var res = validatePack(parsed);
        var html = '';
        res.groups.forEach(function (g) {
            var cls = g.ok ? 'ok' : 'bad';
            var mark = g.ok ? '✓' : '✕';
            html += '<div class="pack-vgroup">' +
                '<div class="pack-vg-head ' + cls + '">' +
                '<span class="pack-vg-ic">' + mark + '</span>' +
                '<span class="pack-vg-title">' + escapeHtml(g.title) + '</span>' +
                '<span class="pack-vg-count">' + g.pass + '/' + g.total + '</span></div>' +
                '<div class="pack-vg-body">';
            g.rows.forEach(function (r) {
                html += '<div class="pack-vrow ' + (r.ok ? 'ok' : 'bad') + '">' +
                    '<span class="pack-vrow-ic">' + (r.ok ? '✓' : '✕') + '</span>' +
                    '<span class="pack-vrow-f">' + escapeHtml(r.label) + '</span>' +
                    (r.detail ? '<span class="pack-vrow-d">' + escapeHtml(r.detail) + '</span>' : '') +
                    '</div>';
            });
            html += '</div></div>';
        });
        resultEl.innerHTML = html;
        if (continueBtn) { continueBtn.disabled = !res.ok; }
        validatedPack = res.ok ? parsed : null;
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

    /** Step 1 → 2: parse + validate, then show the Check pane. */
    function doCheck() {
        var parsed = parseInput();
        renderResult(parsed === undefined ? null : parsed);
        goToStep(2);
    }

    async function doSubmit() {
        var parsed = validatedPack;
        if (!parsed) { goToStep(2); return; }

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
            validatedPack = null;
            loadSubmissions();
        } catch (err) {
            setStatus(errorText({ code: 'GITHUB_ERROR' }, String(err)), 'bad');
            if (submitBtn) { submitBtn.disabled = false; }
        }
    }

    /* ---------------- request mode (#579) ---------------- */

    /** Switch the card between "send a pack" and "ask for a pack". */
    function setMode(mode) {
        var submitPane = el('pack-submit-pane');
        var requestPane = el('pack-request-pane');
        var submitBtn = el('pack-mode-submit-btn');
        var requestBtn = el('pack-mode-request-btn');
        var isRequest = mode === 'request';
        if (submitPane) { submitPane.classList.toggle('hidden', isRequest); }
        if (requestPane) { requestPane.classList.toggle('hidden', !isRequest); }
        [[submitBtn, !isRequest], [requestBtn, isRequest]].forEach(function (pair) {
            if (!pair[0]) { return; }
            pair[0].classList.toggle('active', pair[1]);
            pair[0].setAttribute('aria-selected', pair[1] ? 'true' : 'false');
        });
    }

    function setRequestStatus(msg, kind) {
        var statusEl = el('pack-request-status');
        if (!statusEl) { return; }
        statusEl.textContent = msg || '';
        statusEl.className = 'pack-submit-status' + (kind ? ' pack-submit-status--' + kind : '');
    }

    /** The send button stays disabled until there is a theme to send. */
    function syncRequestButton() {
        var theme = el('pack-request-theme');
        var btn = el('pack-request-btn');
        if (!btn) { return; }
        btn.disabled = !(theme && theme.value.trim());
    }

    async function doRequest() {
        var themeEl = el('pack-request-theme');
        var langEl = el('pack-request-language');
        var notesEl = el('pack-request-notes');
        var btn = el('pack-request-btn');
        var theme = themeEl ? themeEl.value.trim() : '';
        if (!theme) { syncRequestButton(); return; }

        // Client-side cap check mirrors the server so an over-long field fails
        // here with a readable message instead of coming back as a 400.
        if (theme.length > limits.max_theme_chars) {
            setRequestStatus(t('packSubmit.request.tooLong'), 'bad');
            return;
        }
        var notes = notesEl ? notesEl.value.trim() : '';
        if (notes.length > limits.max_notes_chars) {
            setRequestStatus(t('packSubmit.request.tooLong'), 'bad');
            return;
        }

        if (btn) { btn.disabled = true; }
        setRequestStatus(t('packSubmit.request.sending'), 'pending');
        try {
            var resp = await fetch(REQUEST_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    request: {
                        theme: theme,
                        language: (langEl && langEl.value) || 'de',
                        notes: notes
                    }
                })
            });
            var data = {};
            try { data = await resp.json(); } catch (_e) { /* ignore */ }
            if (!resp.ok || !data.ok) {
                setRequestStatus(errorText(data, t('packSubmit.request.failed')), 'bad');
                if (btn) { btn.disabled = false; }
                return;
            }
            var link = data.issue_url ? ' (#' + (data.issue_number || '?') + ')' : '';
            setRequestStatus(t('packSubmit.request.success') + link, 'ok');
            if (themeEl) { themeEl.value = ''; }
            if (notesEl) { notesEl.value = ''; }
            syncRequestButton();
            loadSubmissions();
        } catch (err) {
            setRequestStatus(errorText({ code: 'GITHUB_ERROR' }, String(err)), 'bad');
            if (btn) { btn.disabled = false; }
        }
    }

    function statusLabel(status) {
        var key = 'packSubmit.status.' + (status || 'pending');
        var translated = t(key);
        return translated === key ? (status || 'pending') : translated;
    }

    // Maps a submission status to the timeline node class + status mark.
    function statusMeta(status) {
        if (status === 'accepted') { return { cls: 'a', mark: '✓' }; }
        if (status === 'declined') { return { cls: 'd', mark: '✕' }; }
        return { cls: 'p', mark: '●' };
    }

    async function loadSubmissions() {
        var listEl = el('pack-submit-list');
        var card = el('pack-submit-list-card');
        if (!listEl) { return; }
        try {
            // #356: submissions list is admin-token gated. #608: as a header,
            // not ?token= — the query string ends up in aiohttp's access log
            // and in any reverse proxy in front of HA.
            var _tok = QuizifyUtils.readAdminToken();
            var _init = _tok ? { headers: { 'X-Quizify-Token': _tok } } : {};
            var resp = await fetch(SUBMISSIONS_URL, _init);
            if (!resp.ok) { return; }
            var data = await resp.json();
            var subs = (data && data.submissions) || [];
            if (!subs.length) {
                listEl.innerHTML = '';
                if (card) { card.classList.add('hidden'); }
                return;
            }
            var html = '';
            subs.slice(0, 10).forEach(function (s) {
                var meta = statusMeta(s.status);
                var name = escapeHtml(s.name || '?');
                var title = s.issue_url
                    ? '<a href="' + escapeHtml(s.issue_url) + '" target="_blank" rel="noopener">' + name + '</a>'
                    : name;
                // Records written before #579 carry no `kind` at all — a missing
                // kind is a submission, never an unknown.
                var kindChip = s.kind === 'request'
                    ? '<span class="pack-tli-kind">' + escapeHtml(t('packSubmit.kind.request')) + '</span>'
                    : '';
                var sub = '';
                if (s.issue_number) { sub += '#' + escapeHtml(s.issue_number); }
                html += '<div class="pack-tli ' + meta.cls + '">' +
                    '<div class="pack-tli-node"></div>' +
                    '<div class="pack-tli-t">' + kindChip + title + '</div>' +
                    (sub ? '<div class="pack-tli-m">' + sub + '</div>' : '') +
                    '<div class="pack-tli-stat">' + meta.mark + ' ' + escapeHtml(statusLabel(s.status)) + '</div>' +
                    '</div>';
            });
            listEl.innerHTML = html;
            if (card) { card.classList.remove('hidden'); }
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

        goToStep(1);

        var input = el('pack-submit-input');
        var checkBtn = el('pack-submit-check-btn');
        if (input) {
            input.addEventListener('input', function () {
                if (checkBtn) { checkBtn.disabled = !input.value.trim(); }
                // editing invalidates a prior pass; status clears on re-check
                validatedPack = null;
            });
        }
        if (checkBtn) { checkBtn.addEventListener('click', doCheck); }

        var continueBtn = el('pack-submit-continue-btn');
        if (continueBtn) { continueBtn.addEventListener('click', function () { setStatus(''); goToStep(3); }); }

        var submitBtn = el('pack-submit-btn');
        if (submitBtn) { submitBtn.addEventListener('click', doSubmit); }

        var backBtn = el('pack-submit-back-btn');
        if (backBtn) { backBtn.addEventListener('click', function () { goToStep(1); }); }
        var back2Btn = el('pack-submit-back2-btn');
        if (back2Btn) { back2Btn.addEventListener('click', function () { setStatus(''); goToStep(1); }); }

        // ---- request mode (#579)
        var modeSubmitBtn = el('pack-mode-submit-btn');
        var modeRequestBtn = el('pack-mode-request-btn');
        if (modeSubmitBtn) {
            modeSubmitBtn.addEventListener('click', function () { setMode('submit'); });
        }
        if (modeRequestBtn) {
            modeRequestBtn.addEventListener('click', function () { setMode('request'); });
        }
        setMode('submit');

        var themeEl = el('pack-request-theme');
        if (themeEl) {
            themeEl.addEventListener('input', function () {
                setRequestStatus('');
                syncRequestButton();
            });
        }
        var langEl = el('pack-request-language');
        // Preselect the language the admin UI is already running in — a German
        // host asking for a pack almost never wants an English one.
        if (langEl && window.QuizifyI18n && typeof window.QuizifyI18n.getLanguage === 'function') {
            var cur = window.QuizifyI18n.getLanguage();
            if (cur && langEl.querySelector('option[value="' + cur + '"]')) {
                langEl.value = cur;
            }
        }
        var requestBtn = el('pack-request-btn');
        if (requestBtn) { requestBtn.addEventListener('click', doRequest); }
        syncRequestButton();

        loadSubmissions();
    }

    return {
        init: init,
        validatePack: validatePack,  // exported for tests
        setMode: setMode             // exported for tests
    };
})();

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { window.QuizifyPackSubmit.init(); });
} else {
    window.QuizifyPackSubmit.init();
}
