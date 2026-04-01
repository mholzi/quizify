/**
 * Quizify Service Worker
 *
 * Caches static assets for faster subsequent loads.
 * Uses Cache-First for static assets, Network-First for HTML.
 */
'use strict';

var CACHE_VERSION = 'quizify-v1.0.35';
var MAX_CACHE_ITEMS = 60;

// Critical assets to precache on install
var PRECACHE_ASSETS = [
    '/quizify/static/css/styles.css',
    '/quizify/static/js/i18n.js',
    '/quizify/static/js/utils.js',
    '/quizify/static/js/admin.js',
    '/quizify/static/js/player-utils.js',
    '/quizify/static/js/player-core.js',
    '/quizify/static/js/player-lobby.js',
    '/quizify/static/js/player-game.js',
    '/quizify/static/js/player-reveal.js',
    '/quizify/static/js/player-end.js',
    '/quizify/static/js/vendor/qrcode.min.js',
    '/quizify/static/i18n/de.json',
    '/quizify/static/i18n/en.json',
    '/quizify/static/site.webmanifest',
    '/quizify/static/img/icon-256.png',
    '/quizify/static/img/icon-512.png'
];

/**
 * Install event: Precache critical assets
 */
self.addEventListener('install', function(event) {
    event.waitUntil(
        caches.open(CACHE_VERSION)
            .then(function(cache) {
                return Promise.all(
                    PRECACHE_ASSETS.map(function(url) {
                        return cache.add(url).catch(function(err) {
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

    // HTML pages: Network-First
    var accept = event.request.headers.get('accept') || '';
    if (accept.includes('text/html') || url.pathname.endsWith('.html')) {
        event.respondWith(networkFirst(event.request));
        return;
    }

    // Static assets: Cache-First
    if (url.pathname.startsWith('/quizify/static/')) {
        event.respondWith(cacheFirst(event.request));
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
 * Network-First strategy
 */
function networkFirst(request) {
    return fetch(request)
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
            return caches.match(request);
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
