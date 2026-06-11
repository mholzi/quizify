/**
 * Quizify Service Worker
 *
 * Network-first for everything served by the integration (HTML + CSS + JS),
 * with cache as offline fallback. Cache-first only for cross-origin font
 * assets (Google Fonts, Fontshare) which version themselves via URL.
 *
 * Why network-first for static assets: cache-first served stale CSS / JS
 * after every Quizify update because the SW returned the cached old
 * version even when a new cache-buster was in the HTML reference.
 * Symptom: "CSS works in incognito but not normal Chrome" (#147).
 * Fresh-on-every-load is slower but correct; the user is mostly online
 * during a game session anyway.
 */
'use strict';

// CACHE_VERSION is templated by server/views.py::sw_view at serve time —
// {{VERSION}} is replaced with the integration version from manifest.json.
// Bumping manifest.json invalidates every old SW cache on the next install.
var CACHE_VERSION = 'quizify-v{{ASSET_VER}}';

// Listen for SKIP_WAITING from the page (sw-update.js posts it when the
// user clicks "Reload" in the update banner). Without this the new SW
// would idle as "waiting" until every tab closes.
self.addEventListener('message', function (event) {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});
var MAX_CACHE_ITEMS = 60;

// Critical assets to precache on install. URLs carry the same
// ?v={{ASSET_VER}} cache-buster the HTML pages use, so (a) the cache keys
// match what pages actually request (caches.match is exact on the query
// string — un-versioned precache entries were dead weight that never served
// a versioned request) and (b) a release bump changes every URL.
var PRECACHE_ASSETS = [
    '/quizify/static/css/styles.css?v={{ASSET_VER}}',
    '/quizify/static/js/i18n.js?v={{ASSET_VER}}',
    '/quizify/static/js/utils.js?v={{ASSET_VER}}',
    '/quizify/static/js/icons.js?v={{ASSET_VER}}',
    '/quizify/static/js/admin.js?v={{ASSET_VER}}',
    '/quizify/static/js/pack-submit.js?v={{ASSET_VER}}',
    '/quizify/static/js/player.bundle.js?v={{ASSET_VER}}',
    '/quizify/static/js/sw-update.js?v={{ASSET_VER}}',
    '/quizify/static/js/vendor/qrcode.min.js?v={{ASSET_VER}}',
    '/quizify/static/i18n/de.json?v={{ASSET_VER}}',
    '/quizify/static/i18n/en.json?v={{ASSET_VER}}',
    '/quizify/static/site.webmanifest?v={{ASSET_VER}}',
    '/quizify/static/img/icon-256.png?v={{ASSET_VER}}',
    '/quizify/static/img/icon-512.png?v={{ASSET_VER}}'
];

/**
 * Install event: Precache critical assets.
 *
 * cache: 'reload' forces every precache request past the browser HTTP
 * cache straight to the server. Without it, cache.add() uses the default
 * cache mode, and HA serves /quizify/static/* with
 * Cache-Control: public, max-age=2678400 (31 days) — so a brand-new
 * release's cache got seeded with month-old bytes from the HTTP cache
 * and the "new" install was stale from birth.
 */
self.addEventListener('install', function(event) {
    event.waitUntil(
        caches.open(CACHE_VERSION)
            .then(function(cache) {
                return Promise.all(
                    PRECACHE_ASSETS.map(function(url) {
                        return cache.add(new Request(url, { cache: 'reload' }))
                            .catch(function(err) {
                                console.warn('[SW] Failed to cache:', url, err);
                            });
                    })
                );
            })
            .then(function() {
                return self.skipWaiting();
            })
    );
});

/**
 * Activate event: Clean up old caches
 */
self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys()
            .then(function(cacheNames) {
                return Promise.all(
                    cacheNames
                        .filter(function(name) {
                            return name.startsWith('quizify-') && name !== CACHE_VERSION;
                        })
                        .map(function(name) {
                            console.log('[SW] Deleting old cache:', name);
                            return caches.delete(name);
                        })
                );
            })
            .then(function() {
                return self.clients.claim();
            })
    );
});

/**
 * Fetch event: Handle requests with appropriate caching strategy
 */
self.addEventListener('fetch', function(event) {
    var url = new URL(event.request.url);

    // Skip WebSocket connections
    if (url.pathname.includes('/quizify/ws') || url.protocol === 'ws:' || url.protocol === 'wss:') {
        return;
    }

    // Skip API calls
    if (url.pathname.includes('/api/quizify/')) {
        return;
    }

    // Skip non-GET requests
    if (event.request.method !== 'GET') {
        return;
    }

    // Google Fonts: Cache-First
    if (url.hostname.includes('googleapis.com') || url.hostname.includes('gstatic.com')) {
        event.respondWith(cacheFirst(event.request));
        return;
    }

    // Skip requests to other origins
    if (url.origin !== location.origin) {
        return;
    }

    // HTML pages: Network-First (always try fresh, fall back to cache offline)
    var accept = event.request.headers.get('accept') || '';
    if (accept.includes('text/html') || url.pathname.endsWith('.html')) {
        event.respondWith(networkFirst(event.request));
        return;
    }

    // Static assets: Network-First (was Cache-First; #147 fix).
    // Cache is offline fallback only. The cost is one extra network round-trip
    // per asset on warm load; the benefit is that fresh CSS / JS lands the
    // moment a new version deploys, no SW unregister required.
    if (url.pathname.startsWith('/quizify/static/')) {
        event.respondWith(networkFirst(event.request));
        return;
    }
});

/**
 * Cache-First strategy
 */
function cacheFirst(request) {
    return caches.match(request)
        .then(function(cached) {
            if (cached) {
                return cached;
            }
            return fetch(request)
                .then(function(response) {
                    if (response && response.ok) {
                        var clone = response.clone();
                        caches.open(CACHE_VERSION)
                            .then(function(cache) {
                                cache.put(request, clone);
                                pruneCache();
                            })
                            .catch(function(err) {
                                console.warn('[SW] Cache put failed:', err);
                            });
                    }
                    return response;
                });
        });
}

/**
 * Network-First strategy.
 *
 * Un-versioned same-origin URLs (no ?v= cache-buster) are fetched with
 * cache: 'no-cache' so the browser revalidates with the server instead of
 * answering from its HTTP cache. HA serves static assets with a 31-day
 * max-age, so a plain fetch() would resolve from the HTTP cache without
 * any network round-trip — "network-first" silently became
 * "HTTP-cache-first" and stale assets survived release bumps.
 * Versioned URLs (?v=<version>-<fingerprint>) keep the default cache mode:
 * their URL changes on every release/asset change, so the HTTP cache entry
 * is immutable-per-content and reusing it within a session is correct and
 * fast.
 *
 * Offline fallback: exact cache match first; if the versioned request
 * misses (e.g. cache holds a different ?v=), retry ignoring the query so
 * the user gets *a* working asset offline rather than nothing.
 */
function networkFirst(request) {
    var fetchRequest = request;
    try {
        var reqUrl = new URL(request.url);
        // Navigation (HTML) requests are excluded: re-wrapping a
        // mode:'navigate' Request throws, and the HTML responses already
        // carry server-side no-cache headers.
        if (
            request.mode !== 'navigate' &&
            reqUrl.origin === location.origin &&
            !reqUrl.searchParams.has('v')
        ) {
            fetchRequest = new Request(request, { cache: 'no-cache' });
        }
    } catch (e) { /* fall through with the original request */ }

    return fetch(fetchRequest)
        .then(function(response) {
            if (response && response.ok) {
                var clone = response.clone();
                caches.open(CACHE_VERSION)
                    .then(function(cache) {
                        cache.put(request, clone);
                    })
                    .catch(function(err) {
                        console.warn('[SW] Cache put failed:', err);
                    });
            }
            return response;
        })
        .catch(function() {
            return caches.match(request)
                .then(function(cached) {
                    if (cached) {
                        return cached;
                    }
                    return caches.match(request, { ignoreSearch: true });
                });
        });
}

/**
 * Prune cache to stay under size limit
 */
function pruneCache() {
    caches.open(CACHE_VERSION)
        .then(function(cache) {
            cache.keys().then(function(keys) {
                if (keys.length > MAX_CACHE_ITEMS) {
                    var toDelete = keys.slice(0, keys.length - MAX_CACHE_ITEMS);
                    Promise.all(
                        toDelete.map(function(key) {
                            return cache.delete(key);
                        })
                    );
                }
            });
        })
        .catch(function(err) {
            console.warn('[SW] Prune cache failed:', err);
        });
}
