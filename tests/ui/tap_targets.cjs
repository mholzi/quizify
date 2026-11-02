/*
 * Tap-target check (#1183, UX review 7 §5a). NON-blocking for now.
 *
 * Drives a live dev server (scripts/dev_server.py) through the states a guest
 * and the host actually see — setup, "Custom settings", join, lobby, team
 * pick and name, question, reveal, end — on a 390 × 844 touch phone and a
 * 360 × 740 one, and measures every visible control the way the review did:
 * the hit area including a ::before extension, the gap to the nearest
 * neighbour, and whether any stylesheet gives it :active / :focus-visible.
 *
 * Every finding not in tests/ui/tap_targets_allow.json is printed as a GitHub
 * `::warning::` annotation. The exit code is 0 unless TAP_TARGETS_STRICT=1 —
 * the job warns until the list is worked off, then it becomes a gate.
 *
 *   QUIZIFY_URL=http://127.0.0.1:8080/quizify/ node tests/ui/tap_targets.cjs
 *
 * PLAYWRIGHT_MODULE and CHROME_PATH override where playwright and the browser
 * come from (local runs reuse an installed Chrome). Never forces a hidden
 * view: every state is reached by tapping, the host in its own context.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const BASE = (process.env.QUIZIFY_URL || 'http://127.0.0.1:8080/quizify/').replace(/\/?$/, '/');
const OUT = process.env.TAP_TARGETS_OUT || path.join(process.cwd(), 'tap-targets.json');
const STRICT = process.env.TAP_TARGETS_STRICT === '1';
const ALLOW = JSON.parse(fs.readFileSync(path.join(__dirname, 'tap_targets_allow.json'), 'utf8'));

const MIN_TARGET = 44;   // WCAG 2.5.8 + docs/DESIGN.md "44 × 44"
const MIN_GAP = 8;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Runs in the page. Same rules as the review's measuring script.
function measure() {
    const sel = 'button, a[href], input, select, textarea, [role=button], label.toggle-compact, [tabindex]:not([tabindex="-1"])';
    const rulesActive = [];
    const rulesFocus = [];
    for (const ss of document.styleSheets) {
        let rules;
        try { rules = ss.cssRules; } catch (e) { continue; }
        const walk = (rs) => {
            for (const r of rs) {
                if (r.cssRules && !r.selectorText) { walk(r.cssRules); continue; }
                if (!r.selectorText) continue;
                for (const part of r.selectorText.split(',')) {
                    const p = part.trim();
                    if (p.includes(':active')) {
                        rulesActive.push(p.replace(/:active/g, '').replace(/::?[a-z-]+(\([^)]*\))?$/, '') || '*');
                    }
                    if (p.includes(':focus-visible') || /:focus(?![-\w])/.test(p)) {
                        rulesFocus.push(p.replace(/:focus-visible|:focus(?![-\w])/g, '').replace(/\s*\+\s*[^ ]+$/, '') || '*');
                    }
                }
            }
        };
        walk(rules);
    }
    const matchesAny = (el, list) => list.some((s) => {
        try { return el.matches(s.replace(/:hover/g, '').trim() || '*'); } catch (e) { return false; }
    });
    const els = [...document.querySelectorAll(sel)].filter((el) => {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        if (el.type === 'checkbox' && el.closest('label.toggle-compact')) return false;
        return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'
            && !el.closest('.hidden,[hidden]') && parseFloat(cs.opacity) > 0.05
            && !el.classList.contains('sr-only') && !el.closest('.sr-only');
    });
    const rects = els.map((el) => el.getBoundingClientRect());
    return els.map((el, i) => {
        const r = rects[i];
        const b = getComputedStyle(el, '::before');
        let hw = r.width;
        let hh = r.height;
        if (b.content && b.content !== 'none' && b.position === 'absolute') {
            const n = (v) => (v.endsWith('px') ? parseFloat(v) : 0);
            hw = r.width - n(b.left) - n(b.right);
            hh = r.height - n(b.top) - n(b.bottom);
        }
        let gap = 999;
        rects.forEach((o, j) => {
            if (j === i) return;
            const dx = Math.max(0, Math.max(o.left - r.right, r.left - o.right));
            const dy = Math.max(0, Math.max(o.top - r.bottom, r.top - o.bottom));
            gap = Math.min(gap, Math.hypot(dx, dy));
        });
        const cls = typeof el.className === 'string' ? el.className.split(' ').filter(Boolean)[0] || '' : '';
        const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || '')
            .trim().replace(/\s+/g, ' ').slice(0, 40);
        const inline = el.tagName === 'A' && !!el.closest('p') && getComputedStyle(el).display === 'inline';
        return {
            key: el.id ? '#' + el.id : (cls ? '.' + cls : el.tagName.toLowerCase()),
            field: /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName),
            label, hw: Math.round(hw), hh: Math.round(hh), gap: Math.round(gap),
            active: matchesAny(el, rulesActive), focus: matchesAny(el, rulesFocus), inline
        };
    });
}

(async () => {
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
    const phone = async (w, h) => {
        const c = await browser.newContext({ locale: 'de-DE', viewport: { width: w, height: h }, deviceScaleFactor: 2, hasTouch: true, isMobile: true });
        return c.newPage();
    };
    const states = {};
    const take = async (page, name) => {
        try { states[name] = await page.evaluate(measure); } catch (e) { console.log(`::warning::tap-targets: could not measure ${name}: ${e.message.slice(0, 120)}`); }
    };
    const tapIf = async (page, selector) => {
        const el = await page.$(selector);
        if (el && await el.isVisible()) { await el.tap().catch(() => {}); return true; }
        return false;
    };

    const host = await phone(390, 844);
    await host.goto(BASE + 'admin', { waitUntil: 'networkidle' }); await sleep(1500);
    await take(host, 'host-setup');
    await tapIf(host, '#setup-tweak-btn'); await sleep(800);
    await tapIf(host, '.preset-card[data-preset="eigene"]'); await sleep(800);
    await take(host, 'host-setup-custom');
    await host.goto(BASE + 'admin', { waitUntil: 'networkidle' }); await sleep(1500);
    await tapIf(host, '#start-game-btn'); await sleep(1500);

    const p1 = await phone(390, 844);
    const p2 = await phone(360, 740);
    await p1.goto(BASE + 'player', { waitUntil: 'networkidle' }); await sleep(1000);
    await take(p1, 'player-join');
    for (const [p, name] of [[p1, 'Lena'], [p2, 'Opa Karl']]) {
        if (p === p2) { await p.goto(BASE + 'player', { waitUntil: 'networkidle' }); await sleep(800); }
        await p.fill('#name-input', name); await p.press('#name-input', 'Enter'); await sleep(1800);
    }
    await take(p1, 'player-lobby');
    await tapIf(p1, '#team-together-btn'); await sleep(800);
    await take(p1, 'player-team-pick');
    await tapIf(p1, '#team-open-btn'); await sleep(600);
    await take(p1, 'player-team-name');
    await tapIf(p1, '#team-name-cancel'); await sleep(400);

    await host.reload({ waitUntil: 'networkidle' }); await sleep(1800);
    await take(host, 'host-lobby');
    await tapIf(host, '#start-gameplay-btn'); await sleep(6000);
    await take(p2, 'player-question');
    await take(host, 'host-question');
    await tapIf(host, '#admin-skip-btn'); await sleep(3000);
    await take(p2, 'player-reveal');
    await take(host, 'host-reveal');
    if (await tapIf(host, '#end-game-btn')) {
        await sleep(800);
        await tapIf(host, '[role=dialog]:not(.hidden) .btn-danger');
    }
    await sleep(3000);
    await take(host, 'host-end');
    await take(p2, 'player-end');
    await browser.close();

    // One row per control (the same control seen in two states counts once).
    const seen = new Map();
    for (const [state, rows] of Object.entries(states)) {
        for (const row of rows) {
            const id = row.key + '|' + row.label;
            if (!seen.has(id)) seen.set(id, Object.assign({ state }, row));
        }
    }
    const findings = [];
    for (const row of seen.values()) {
        // A string in the allow list excuses the control outright; an object
        // excuses only the named checks (target / gap / active / focus).
        const allow = ALLOW[row.key];
        const excused = (kind) => typeof allow === 'string' || (allow && allow[kind]);
        const reasons = [];
        if (Math.min(row.hw, row.hh) < MIN_TARGET && !row.inline && !excused('target')) reasons.push(`target ${row.hw}×${row.hh}`);
        if (row.gap < MIN_GAP && !excused('gap')) reasons.push(`gap ${row.gap}px`);
        // A form field shows focus, not a press; a link in running text neither.
        if (!row.active && !row.inline && !row.field && !excused('active')) reasons.push('no :active');
        if (!row.focus && !excused('focus')) reasons.push('no :focus-visible');
        if (!reasons.length) continue;
        findings.push(`${row.state} ${row.key} "${row.label}": ${reasons.join(', ')}`);
    }
    const controls = seen.size;
    const rows = [...seen.values()];
    const summary = {
        controls,
        under44: rows.filter((r) => Math.min(r.hw, r.hh) < MIN_TARGET).length,
        gapUnder8: rows.filter((r) => r.gap < MIN_GAP).length,
        withoutActive: rows.filter((r) => !r.active).length,
        findings: findings.length
    };
    fs.writeFileSync(OUT, JSON.stringify({ summary, states, findings }, null, 1));
    console.log('tap-targets:', JSON.stringify(summary));
    findings.forEach((f) => console.log('::warning title=tap target::' + f));
    if (controls < 30) {
        console.log(`::warning::tap-targets: only ${controls} controls measured — the flow did not reach its states`);
    }
    process.exit(STRICT && (findings.length || controls < 30) ? 1 : 0);
})().catch((e) => {
    console.log('::warning::tap-targets run failed: ' + e.message.slice(0, 300));
    process.exit(STRICT ? 1 : 0);
});
