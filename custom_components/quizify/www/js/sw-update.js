/**
 * Quizify Service Worker registration + update prompt
 *
 * Registers the SW on every page, and shows a small "Neue Version
 * verfügbar — Neu laden?" banner when a new SW is waiting to activate.
 * Polls for updates on visibilitychange (cheap, only on tab-focus) so
 * the banner appears the next time the host comes back to the tab
 * after deploying — no manual refresh needed.
 *
 * Self-contained so admin.html / player.html / dashboard.html can all
 * drop a single <script> tag.
 */
(function () {
    'use strict';

    if (!('serviceWorker' in navigator)) return;

    function _t(key, fallback) {
        if (window.QuizifyI18n && typeof window.QuizifyI18n.t === 'function') {
            var v = window.QuizifyI18n.t(key);
            if (v && v !== key) return v;
        }
        return fallback;
    }

    function showUpdateBanner(worker) {
        if (document.getElementById('quizify-sw-update-banner')) return;
        var banner = document.createElement('div');
        banner.id = 'quizify-sw-update-banner';
        banner.setAttribute('role', 'status');
        banner.setAttribute('aria-live', 'polite');
        banner.style.cssText = [
            'position:fixed', 'left:50%', 'transform:translateX(-50%)',
            'bottom:16px', 'z-index:99999',
            'background:var(--color-bg-surface, #fff)',
            'color:var(--color-text-primary, #2A2820)',
            'border:1px solid var(--color-border-medium, #D4CEBC)',
            'border-radius:12px',
            'padding:12px 16px',
            'box-shadow:0 6px 20px rgba(42, 40, 32, 0.18)',
            'display:flex', 'gap:12px', 'align-items:center',
            'font-family:var(--font-body, system-ui)',
            'font-size:0.95rem',
            'max-width:calc(100vw - 32px)'
        ].join(';');

        var msg = document.createElement('span');
        msg.textContent = _t('common.updateAvailable', 'Neue Version verfügbar');

        var reloadBtn = document.createElement('button');
        reloadBtn.type = 'button';
        reloadBtn.textContent = _t('common.reload', 'Neu laden');
        reloadBtn.style.cssText = [
            'background:var(--color-accent-primary, #E88A7F)',
            'color:#fff',
            'border:0',
            'border-radius:8px',
            'padding:8px 14px',
            'font-weight:600',
            'cursor:pointer',
            'font-family:inherit'
        ].join(';');
        reloadBtn.addEventListener('click', function () {
            // Tell waiting SW to take over; controllerchange handler reloads.
            if (worker) worker.postMessage({ type: 'SKIP_WAITING' });
            // Fallback: reload after 1.5s if controllerchange never fires.
            setTimeout(function () { window.location.reload(); }, 1500);
        });

        var dismissBtn = document.createElement('button');
        dismissBtn.type = 'button';
        dismissBtn.textContent = '✕';
        dismissBtn.setAttribute('aria-label', _t('common.close', 'Schließen'));
        dismissBtn.style.cssText = [
            'background:transparent',
            'border:0',
            'cursor:pointer',
            'font-size:1.1rem',
            'color:var(--color-text-muted, #6E6A5C)',
            'padding:4px 6px'
        ].join(';');
        dismissBtn.addEventListener('click', function () { banner.remove(); });

        banner.appendChild(msg);
        banner.appendChild(reloadBtn);
        banner.appendChild(dismissBtn);
        document.body.appendChild(banner);
    }

    function trackWaiting(reg) {
        if (reg.waiting && navigator.serviceWorker.controller) {
            showUpdateBanner(reg.waiting);
        }
        reg.addEventListener('updatefound', function () {
            var installing = reg.installing;
            if (!installing) return;
            installing.addEventListener('statechange', function () {
                if (installing.state === 'installed' && navigator.serviceWorker.controller) {
                    showUpdateBanner(installing);
                }
            });
        });
    }

    navigator.serviceWorker.register('/quizify/static/sw.js')
        .then(function (reg) {
            trackWaiting(reg);

            // Re-check for updates when the user comes back to the tab.
            // Cheap and matches the typical "deploy then look at TV again"
            // mental model for a HA integration.
            document.addEventListener('visibilitychange', function () {
                if (document.visibilityState === 'visible') {
                    reg.update().catch(function () { /* ignore */ });
                }
            });
        })
        .catch(function (err) {
            console.warn('[SW] registration failed', err);
        });

    // When the new SW takes control, reload so the new code runs.
    var _refreshing = false;
    navigator.serviceWorker.addEventListener('controllerchange', function () {
        if (_refreshing) return;
        _refreshing = true;
        window.location.reload();
    });
})();
