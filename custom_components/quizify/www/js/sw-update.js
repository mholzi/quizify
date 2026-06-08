/**
 * Quizify Service Worker registration
 *
 * Registers the SW for PWA install + offline asset caching, and quietly
 * refreshes it on tab-focus. That's it — no "New version available" banner
 * and no forced reload.
 *
 * Why no banner: the asset cache-buster is now a content fingerprint
 * (?v=<version>-<hash>), so the next natural page load already pulls fresh
 * CSS/JS/i18n. There's nothing to nag about, and dropping the auto-reload
 * means an always-on host screen can't reload itself mid-game. A new SW
 * activates silently in the background; its assets land on the next load.
 *
 * Self-contained so admin.html / player.html / dashboard.html can all drop a
 * single <script> tag.
 */
(function () {
    'use strict';

    if (!('serviceWorker' in navigator)) return;

    navigator.serviceWorker.register('/quizify/static/sw.js')
        .then(function (reg) {
            // Check for a newer SW when the host comes back to the tab. The new
            // SW installs and activates on its own; fresh assets are served on
            // the next page load. No banner, no forced reload.
            document.addEventListener('visibilitychange', function () {
                if (document.visibilityState === 'visible') {
                    reg.update().catch(function () { /* ignore */ });
                }
            });
        })
        .catch(function (err) {
            console.warn('[SW] registration failed', err);
        });
})();
