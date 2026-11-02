/**
 * Quizify Internationalization (i18n) Module
 * Provides translation functionality for all UI text
 */
window.QuizifyI18n = (function() {
    'use strict';

    var currentLanguage = 'en';
    var translations = {};
    var fallbackTranslations = {};
    var isLoaded = false;
    var loadPromise = null;
    var latestLoad = null;

    async function fetchTranslations(langCode) {
        try {
            // Cache-bust via the page's cache-buster query param (set by the
            // server-side template). Without this the browser HTTP cache
            // pins the old JSON across releases, so translation fixes ship
            // but don't surface until users clear cache.
            var bust = '';
            try {
                var anyScript = document.querySelector('script[src*="i18n.js?v="]');
                if (anyScript) {
                    var m = anyScript.src.match(/[?&]v=([^&]+)/);
                    if (m) bust = '?v=' + encodeURIComponent(m[1]);
                }
            } catch (_e) { /* ignore */ }
            var response = await fetch('/quizify/static/i18n/' + langCode + '.json' + bust);
            if (!response.ok) {
                console.warn('[i18n] Failed to load ' + langCode + '.json:', response.status);
                return {};
            }
            return await response.json();
        } catch (err) {
            console.warn('[i18n] Error loading ' + langCode + '.json:', err);
            return {};
        }
    }

    // #1055: a load only installs its bundle if its language is still the
    // one asked for when the bundle arrives. Two loads overlap whenever the
    // game language lands while the page is still fetching the browser's —
    // the host's tab right after the redirect from the admin page, any phone
    // loading the page mid-game. The newer one (English, already held as the
    // fallback) finished first, and the older one then wrote its German
    // bundle over it: getLanguage() said "en", every label said German, and
    // nothing corrected it, because each later game_state saw the language
    // already "matching" and skipped the switch.
    async function loadTranslations() {
        var lang = currentLanguage;
        if (Object.keys(fallbackTranslations).length === 0) {
            var fallback = await fetchTranslations('en');
            if (Object.keys(fallbackTranslations).length === 0) {
                fallbackTranslations = fallback;
            }
        }
        var loaded = (lang === 'en')
            ? fallbackTranslations
            : await fetchTranslations(lang);
        if (lang !== currentLanguage) return;  // superseded while on the wire
        translations = loaded;
        isLoaded = true;
    }

    function getNestedValue(obj, key) {
        if (!obj || !key) return undefined;
        var parts = key.split('.');
        var current = obj;
        for (var i = 0; i < parts.length; i++) {
            if (current === undefined || current === null) return undefined;
            current = current[parts[i]];
        }
        return current;
    }

    // #1177: numbers and dates speak the GAME language, not the browser's.
    // A German room reads "384.400" and "3,5", a Spanish one "12.345" — the
    // String(n) the TV used to print gave every room "384400" and "3.5".
    var _numberFormats = {};

    function formatNumber(value, options) {
        var num = Number(value);
        if (value === null || value === undefined || value === '' || !isFinite(num)) {
            return value === null || value === undefined ? '' : String(value);
        }
        var cacheKey = currentLanguage + '|' + (options ? JSON.stringify(options) : '');
        try {
            var fmt = _numberFormats[cacheKey];
            if (!fmt) {
                fmt = new Intl.NumberFormat(currentLanguage, options || { maximumFractionDigits: 3 });
                _numberFormats[cacheKey] = fmt;
            }
            return fmt.format(num);
        } catch (_e) {
            return String(num);
        }
    }

    function formatDate(value, options) {
        var date = value instanceof Date ? value : new Date(value);
        if (isNaN(date.getTime())) return '';
        try {
            return new Intl.DateTimeFormat(currentLanguage, options || {
                dateStyle: 'medium', timeStyle: 'short'
            }).format(date);
        } catch (_e) {
            return date.toISOString().slice(0, 16).replace('T', ' ');
        }
    }

    var _pluralRules = {};

    function _pluralCategory(count) {
        try {
            var rules = _pluralRules[currentLanguage];
            if (!rules) {
                rules = new Intl.PluralRules(currentLanguage);
                _pluralRules[currentLanguage] = rules;
            }
            return rules.select(count);
        } catch (_e) {
            return count === 1 ? 'one' : 'other';
        }
    }

    // Index of the "}" that closes the "{" at `open`, or -1.
    function _matchBrace(text, open) {
        var depth = 0;
        for (var i = open; i < text.length; i++) {
            var ch = text.charAt(i);
            if (ch === '{') depth++;
            else if (ch === '}') {
                depth--;
                if (depth === 0) return i;
            }
        }
        return -1;
    }

    // #1177: ICU-style plurals, the subset the bundles need —
    //   {count, plural, =0{…} one{…} other{…}}
    // "#" inside a branch is the count, formatted for the language; other
    // {params} inside a branch are filled afterwards like anywhere else.
    // Before this every plural was a hand-built pair (lobby.alsoOne /
    // lobby.alsoMany), which is how "1 pts" and "faltan 1 más" got shipped.
    var PLURAL_HEAD = /\{\s*(\w+)\s*,\s*plural\s*,/g;

    function _expandPlurals(text, params) {
        if (text.indexOf('plural') === -1) return text;
        var out = '';
        var cursor = 0;
        PLURAL_HEAD.lastIndex = 0;
        var head;
        while ((head = PLURAL_HEAD.exec(text)) !== null) {
            var start = head.index;
            var end = _matchBrace(text, start);
            if (end === -1) break;  // malformed: leave the rest untouched
            var name = head[1];
            var raw = params ? params[name] : undefined;
            var count = Number(raw);
            var hasCount = raw !== undefined && raw !== null && raw !== '' && isFinite(count);
            var branches = {};
            var i = start + head[0].length;
            while (i < end) {
                var open = text.indexOf('{', i);
                if (open === -1 || open > end) break;
                var selector = text.slice(i, open).trim();
                var close = _matchBrace(text, open);
                if (close === -1 || close > end) break;
                if (selector) branches[selector] = text.slice(open + 1, close);
                i = close + 1;
            }
            var chosen;
            if (hasCount && branches['=' + count] !== undefined) {
                chosen = branches['=' + count];
            } else if (hasCount && branches[_pluralCategory(count)] !== undefined) {
                chosen = branches[_pluralCategory(count)];
            } else {
                chosen = branches.other !== undefined ? branches.other : '';
            }
            chosen = _expandPlurals(chosen, params);
            if (hasCount) chosen = chosen.replace(/#/g, formatNumber(count));
            out += text.slice(cursor, start) + chosen;
            cursor = end + 1;
            PLURAL_HEAD.lastIndex = cursor;
        }
        return out + text.slice(cursor);
    }

    function t(key, params) {
        var value = getNestedValue(translations, key);
        if (value === undefined && currentLanguage !== 'en') {
            value = getNestedValue(fallbackTranslations, key);
        }
        if (value === undefined) {
            return key;
        }
        if (typeof value === 'string') {
            value = _expandPlurals(value, params);
        }
        if (params && typeof value === 'string') {
            Object.keys(params).forEach(function(param) {
                var raw = params[param];
                // Numbers are written in the language's own way (#1177).
                var shown = (typeof raw === 'number') ? formatNumber(raw) : raw;
                value = value.replace(new RegExp('\\{' + param + '\\}', 'g'), shown);
            });
        }
        return value;
    }

    function getErrorMessage(code) {
        return t('errors.' + code) || t('errors.UNKNOWN');
    }

    var SUPPORTED_LANGUAGES = ['en', 'de', 'es'];

    async function setLanguage(langCode) {
        if (SUPPORTED_LANGUAGES.indexOf(langCode) === -1) {
            langCode = 'en';
        }
        if (langCode === currentLanguage && isLoaded) return;
        currentLanguage = langCode;
        var mine = latestLoad = loadTranslations();
        await mine;
        // Superseded mid-flight (#1055): resolve only once the language that
        // won is in, so a caller that sweeps the page on resolve — init()'s
        // does — paints from the bundle that is actually active.
        while (latestLoad !== mine) {
            mine = latestLoad;
            await mine;
        }
    }

    function getLanguage() {
        return currentLanguage;
    }

    // #734: a data-i18n key that no bundle carries used to fail in complete
    // silence — t() returns the key, initPageTranslations leaves the element
    // alone, and the English markup ships to a German television. The pill
    // pointed at "dashboard.reconnecting" for months without a single
    // complaint from the code. Say it out loud; the real net is
    // tests/test_television_speaks_the_room_language_733_734.py, which fails
    // the build before it reaches a living room.
    var warnedMissingKeys = {};

    function warnMissingKey(attr, key) {
        if (warnedMissingKeys[key]) return;
        warnedMissingKeys[key] = true;
        console.warn('[i18n] no translation for ' + attr + '="' + key + '" — element left untranslated');
    }

    // #776: a data-i18n key that needs {placeholders} used to be untranslatable
    // by the page sweep — the only way to fill one was a one-off t(key, params)
    // call writing textContent, and a line written that way is frozen in
    // whichever bundle happened to be loaded at that instant (see
    // renderAllTime in player-lobby.js: the phone joined in English, the game
    // language arrived a heartbeat later, and the standing stayed English on a
    // German screen). Carrying the params on the element as JSON lets the sweep
    // re-render the line in the new language with no help from the caller.
    function _readI18nParams(el) {
        var raw = el.getAttribute('data-i18n-params');
        if (!raw) return null;
        try {
            var parsed = JSON.parse(raw);
            return (parsed && typeof parsed === 'object') ? parsed : null;
        } catch (_e) {
            return null;  // malformed attribute: translate without params
        }
    }

    function initPageTranslations(root) {
        var scope = root || document;
        scope.querySelectorAll('[data-i18n]').forEach(function(el) {
            var key = el.getAttribute('data-i18n');
            if (key) {
                var translated = t(key, _readI18nParams(el));
                if (translated !== key) el.textContent = translated;
                else warnMissingKey('data-i18n', key);
            }
        });
        // #1177: a two-line headline is ONE key with a "\n" where the line
        // breaks, so the translator decides where it falls — it used to be
        // two keys (setup.heroLine1/2) glued together in the markup, which
        // fixes the word order of every language to the English one. Lines
        // after the first carry the accent colour, as the old second span did.
        scope.querySelectorAll('[data-i18n-lines]').forEach(function(el) {
            var key = el.getAttribute('data-i18n-lines');
            if (!key) return;
            var translated = t(key);
            if (translated === key) { warnMissingKey('data-i18n-lines', key); return; }
            var lines = String(translated).split('\n');
            el.textContent = '';
            lines.forEach(function(line, i) {
                if (i > 0) el.appendChild(document.createElement('br'));
                var span = document.createElement('span');
                if (i > 0) span.className = 'accent';
                span.textContent = line;
                el.appendChild(span);
            });
        });
        scope.querySelectorAll('[data-i18n-placeholder]').forEach(function(el) {
            var key = el.getAttribute('data-i18n-placeholder');
            if (key) {
                var translated = t(key);
                if (translated !== key) el.placeholder = translated;
                else warnMissingKey('data-i18n-placeholder', key);
            }
        });
        scope.querySelectorAll('[data-i18n-title]').forEach(function(el) {
            var key = el.getAttribute('data-i18n-title');
            if (key) {
                var translated = t(key);
                if (translated !== key) el.title = translated;
                else warnMissingKey('data-i18n-title', key);
            }
        });
        scope.querySelectorAll('[data-i18n-aria-label]').forEach(function(el) {
            var key = el.getAttribute('data-i18n-aria-label');
            if (key) {
                var translated = t(key);
                if (translated !== key) el.setAttribute('aria-label', translated);
                else warnMissingKey('data-i18n-aria-label', key);
            }
        });
        // Sync <html lang> with the active language so screen readers
        // and translation extensions get the correct hint.
        if (document.documentElement && currentLanguage) {
            document.documentElement.lang = currentLanguage;
        }
    }

    function detectBrowserLanguage() {
        var browserLang = navigator.language || navigator.userLanguage || 'en';
        var langLower = browserLang.toLowerCase();
        if (langLower.startsWith('de')) return 'de';
        if (langLower.startsWith('es')) return 'es';
        return 'en';
    }

    // Preferred UI language for HOST screens (launcher, admin, dashboard):
    // Home Assistant's configured language wins, injected server-side into the
    // quizify-ha-lang meta tag. Falls back to browser locale (e.g. standalone
    // dev server where the tag is empty). Without this, host screens flashed
    // English then flipped to the browser language, ignoring the HA setting.
    function getPreferredLanguage() {
        try {
            var meta = document.querySelector('meta[name="quizify-ha-lang"]');
            var haLang = meta ? meta.getAttribute('content') : '';
            // Guard against an unsubstituted {{HA_LANG}} token.
            if (haLang && haLang.indexOf('{') === -1) {
                var hl = haLang.toLowerCase();
                if (hl.indexOf('de') === 0) return 'de';
                if (hl.indexOf('es') === 0) return 'es';
                return 'en';
            }
        } catch (_e) { /* ignore */ }
        return detectBrowserLanguage();
    }

    async function init(langCode) {
        if (loadPromise) return loadPromise;
        var lang = langCode || detectBrowserLanguage();
        loadPromise = setLanguage(lang);
        await loadPromise;
    }

    function isReady() {
        return isLoaded;
    }

    return {
        t: t,
        formatNumber: formatNumber,
        formatDate: formatDate,
        getErrorMessage: getErrorMessage,
        setLanguage: setLanguage,
        getLanguage: getLanguage,
        initPageTranslations: initPageTranslations,
        detectBrowserLanguage: detectBrowserLanguage,
        getPreferredLanguage: getPreferredLanguage,
        init: init,
        isReady: isReady
    };
})();

window.t = window.QuizifyI18n.t;
