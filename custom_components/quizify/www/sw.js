/**
 * Quizify Service Worker
 *
 * Network-first for everything served by the integration (HTML + CSS + JS),
 * with cache as offline fallback. Cache-first only for the self-hosted font
 * files, whose bytes never change under a given filename (#737, #738).
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

// ---------------------------------------------------------------------------
// Precache (#791)
//
// This used to be ONE list for all three surfaces: a player phone precached
// admin.js (183 KB) and pack-submit.js (22 KB) it never executes, both icon
// PNGs and both i18n bundles — 1,148,049 bytes for every phone that joins.
// With thirteen phones plus the TV that is a ~25 MB burst off the HA box over
// party Wi-Fi during the join minute, exactly when the host needs the lobby to
// fill.
//
// So: a core list every surface really loads, plus a per-page extra resolved
// from the window clients at install time. Nothing is lost when the surface
// cannot be determined — the fetch handler is network-first and caches at
// runtime, so a missing precache entry costs a round trip on first use, never
// correctness.
//
// URLs carry the same ?v={{ASSET_VER}} cache-buster the HTML pages use, so
// (a) the cache keys match what pages actually request (caches.match is exact
// on the query string — un-versioned precache entries were dead weight that
// never served a versioned request) and (b) a release bump changes every URL.
// ---------------------------------------------------------------------------

// Substituted by server/views.py::sw_view from HA's configured language,
// normalised to the shipped UI set (de/en/es).
var DEFAULT_LANG = '{{DEFAULT_LANG}}';

// Loaded by admin.html, player.html AND dashboard.html.
var PRECACHE_CORE = [
    '/quizify/static/css/styles.css?v={{ASSET_VER}}',
    '/quizify/static/js/i18n.js?v={{ASSET_VER}}',
    '/quizify/static/js/utils.js?v={{ASSET_VER}}',
    // #787: the shared socket core + renderers. Core, not per-page —
    // all three surfaces load it, ahead of their own script.
    '/quizify/static/js/common.bundle.js?v={{ASSET_VER}}',
    '/quizify/static/js/sw-update.js?v={{ASSET_VER}}',
    '/quizify/static/js/vendor/qrcode.min.js?v={{ASSET_VER}}',
    // en.json is not optional: i18n.js loads it as the fallback dictionary
    // whatever the active language is. The active bundle is appended below
    // when it is not English.
    '/quizify/static/i18n/en.json?v={{ASSET_VER}}',
    '/quizify/static/site.webmanifest?v={{ASSET_VER}}',
    // icon-256 is the favicon on all three pages. icon-512 (107 KB) is
    // referenced only from the webmanifest, i.e. at install/splash time, so it
    // is left to the runtime cache instead of being pushed to every phone.
    '/quizify/static/img/icon-256.png?v={{ASSET_VER}}',
    // Fonts carry no ?v= — the CSS asks for them plain, and caches.match
    // is exact on the query string. A font file's bytes never change
    // under a given name, so there is nothing to bust.
    '/quizify/static/fonts/dm-sans-latin.woff2',
    '/quizify/static/fonts/jetbrains-mono-latin.woff2'
];

// Extras per surface, keyed by the page path under /quizify/.
var PRECACHE_BY_PAGE = {
    player: [
        '/quizify/static/js/icons.js?v={{ASSET_VER}}',
        '/quizify/static/js/player.bundle.js?v={{ASSET_VER}}'
    ],
    admin: [
        '/quizify/static/js/icons.js?v={{ASSET_VER}}',
        '/quizify/static/js/admin.js?v={{ASSET_VER}}',
        '/quizify/static/js/pack-submit.js?v={{ASSET_VER}}'
    ],
    // The TV loads i18n.js / utils.js / common.bundle.js / qrcode from the core
    // list and keeps the rest of its code inline in dashboard.html.
    dashboard: []
};

/**
 * Map a client URL to one of the keys in PRECACHE_BY_PAGE, or null.
 *
 * The three surfaces are served from fixed paths (/quizify/player,
 * /quizify/admin, /quizify/dashboard — see server/views.py ROUTES), so this is
 * a lookup, not a guess. /quizify/launcher and /quizify/analytics resolve to
 * null on purpose: everything they load is already in the core list.
 */
function pageForClientUrl(url) {
    var path;
    try {
        path = new URL(url).pathname;
    } catch (e) {
        return null;
    }
    if (path.charAt(path.length - 1) === '/') {
        path = path.slice(0, -1);
    }
    var name = path.slice(path.lastIndexOf('/') + 1);
    return Object.prototype.hasOwnProperty.call(PRECACHE_BY_PAGE, name) ? name : null;
}

/**
 * The URLs to precache for the surfaces currently open.
 *
 * Union over the open windows, so a host with the admin page and the TV
 * dashboard on one device still gets both. No recognised window (the update
 * ran with nothing open) falls back to the core list alone — never to
 * "everything", which is the bug this replaces.
 */
function precacheListForClientUrls(urls) {
    var out = PRECACHE_CORE.slice();
    if (DEFAULT_LANG && DEFAULT_LANG !== 'en' && /^[a-z]{2}$/.test(DEFAULT_LANG)) {
        out.push('/quizify/static/i18n/' + DEFAULT_LANG + '.json?v={{ASSET_VER}}');
    }
    var seen = {};
    (urls || []).forEach(function (url) {
        var page = pageForClientUrl(url);
        if (!page || seen[page]) {
            return;
        }
        seen[page] = true;
        PRECACHE_BY_PAGE[page].forEach(function (asset) {
            if (out.indexOf(asset) === -1) {
                out.push(asset);
            }
        });
    });
    return out;
}

/**
 * Build the install-time Request for one precache URL.
 *
 * Versioned URLs (?v=<version>-<fingerprint>) are immutable per content: that
 * exact URL can only ever mean those exact bytes, so the HTTP cache entry the
 * page just filled is by definition the right answer. A plain Request reuses
 * it instead of pulling the same 330 KB bundle down a second time — which is
 * what `cache: 'reload'` did on every first visit and after every release.
 *
 * Un-versioned URLs (the fonts) keep `cache: 'reload'`: HA serves
 * /quizify/static/* with Cache-Control: public, max-age=2678400 (31 days), so
 * without it a fresh cache could be seeded from a month-old HTTP cache entry
 * and be stale from birth.
 */
function precacheRequest(url) {
    if (url.indexOf('?v=') !== -1) {
        return new Request(url);
    }
    return new Request(url, { cache: 'reload' });
}

/**
 * Install event: Precache the assets this device's surface actually loads.
 *
 * includeUncontrolled is what makes matchAll see the page that just registered
 * us — during install this worker controls nothing yet, so without it the list
 * is always empty and every device would fall back to the core list.
 */
self.addEventListener('install', function(event) {
    var windows;
    try {
        windows = self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    } catch (e) {
        // A worker with no clients API at all: precache the core list rather
        // than nothing.
        windows = Promise.resolve([]);
    }
    event.waitUntil(
        windows
            .catch(function() { return []; })
            .then(function(clients) {
                var urls = precacheListForClientUrls(
                    clients.map(function(client) { return client.url; })
                );
                return caches.open(CACHE_VERSION).then(function(cache) {
                    return Promise.all(
                        urls.map(function(url) {
                            return cache.add(precacheRequest(url))
                                .catch(function(err) {
                                    console.warn('[SW] Failed to cache:', url, err);
                                });
                        })
                    );
                });
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

    // Skip requests to other origins. Nothing the game loads is
    // cross-origin any more — the fonts moved in-tree (#737, #738).
    if (url.origin !== location.origin) {
        return;
    }

    // HTML pages: Network-First (always try fresh, fall back to cache offline)
    var accept = event.request.headers.get('accept') || '';
    if (accept.includes('text/html') || url.pathname.endsWith('.html')) {
        event.respondWith(networkFirst(event.request));
        return;
    }

    // Fonts: Cache-First. Unlike CSS / JS they are content-stable — a new
    // face means a new filename — so there is no stale-asset trap here, and
    // skipping the round trip is exactly what keeps the first paint quick.
    if (url.pathname.startsWith('/quizify/static/fonts/')) {
        event.respondWith(cacheFirst(event.request));
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
                        return cache.put(request, clone);
                    })
                    .then(function() {
                        // Keep the runtime cache bounded (issue #257) —
                        // networkFirst put on every fetch without ever
                        // pruning, so the cache grew unbounded.
                        pruneCache();
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
