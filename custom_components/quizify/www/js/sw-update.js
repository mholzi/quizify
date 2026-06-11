/**
 * Quizify Service Worker registration + safe auto-reload
 *
 * Registers the SW for PWA install + offline asset caching, refreshes it on
 * tab-focus, and — new in #215 — reloads the page once when a new SW takes
 * control, but ONLY while the screen is idle.
 *
 * Why the auto-reload: the asset cache-buster is a content fingerprint
 * (?v=<version>-<hash>), so the next *natural* page load pulls fresh CSS/JS.
 * But on an always-open PWA / tablet that natural load never happens, so the
 * host keeps running stale admin.js/CSS forever even after a deploy. #215
 * fixes that by reloading when the SW controller changes — without ever
 * reloading mid-game.
 *
 * Idle gate: the shared script asks the host app via window.quizifyIsIdleForReload()
 * (a function returning boolean). Each app (admin / player / dashboard) wires
 * this to its own screen/phase notion and returns true only on idle screens
 * (setup / lobby / join / end / recap), false during an active question, game
 * round, or lightning round. If the hook is absent we fall back to "no reload"
 * — a missed refresh is harmless (next natural load fixes it), a mid-game
 * reload is not.
 *
 * Self-contained so admin.html / player.html / dashboard.html can all drop a
 * single <script> tag.
 */
(function () {
    'use strict';

    if (!('serviceWorker' in navigator)) return;

    // Module-level guard: a controllerchange-triggered reload must happen at
    // most once per page life, so we never loop (the reload installs a fresh
    // controller, which could otherwise fire controllerchange again).
    var reloadTriggered = false;

    // Decide whether it's safe to reload right now.
    //   - Prefer the explicit host hook window.quizifyIsIdleForReload().
    //   - If the hook is missing, default to SAFE = do not reload. A stale
    //     frontend self-heals on the next natural load; a mid-game reload
    //     (wrong "idle" guess) would visibly break the session.
    function isIdleForReload() {
        try {
            if (typeof window.quizifyIsIdleForReload === 'function') {
                return !!window.quizifyIsIdleForReload();
            }
        } catch (e) {
            // A throwing hook counts as "not idle" — stay conservative.
            return false;
        }
        return false;
    }

    function maybeReload() {
        if (reloadTriggered) return;
        if (!isIdleForReload()) return;
        reloadTriggered = true;
        // Full reload so the fresh SW serves the new fingerprinted assets.
        window.location.reload();
    }

    // A new SW took control of this page → its (changed) assets are now what
    // the network-first SW will serve. Reload once, but only while idle.
    navigator.serviceWorker.addEventListener('controllerchange', maybeReload);

    // updateViaCache: 'none' — the SW script (and any future importScripts)
    // must NEVER be satisfied from the browser HTTP cache. The server already
    // sends no-cache headers on sw.js, but older browsers honored the HTTP
    // cache for up to 24h on the SW script; an HTTP-cached sw.js carries the
    // OLD CACHE_VERSION, so the old cache never gets invalidated and a new
    // release can't reset the client. Belt-and-braces for the same invariant.
    navigator.serviceWorker.register('/quizify/static/sw.js', { updateViaCache: 'none' })
        .then(function (reg) {
            // Deterministic update check on every page load — don't rely on
            // the browser's own (possibly throttled, up-to-24h) navigation
            // check. A release bump is detected at the latest on the next
            // page load: fresh sw.js → new CACHE_VERSION → install/activate
            // deletes every old quizify-* cache.
            reg.update().catch(function () { /* ignore */ });

            // Check for a newer SW when the host comes back to the tab. The new
            // SW installs and activates on its own (skipWaiting + clients.claim),
            // which fires controllerchange → maybeReload above.
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
