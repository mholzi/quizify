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

    async function loadTranslations() {
        if (Object.keys(fallbackTranslations).length === 0) {
            fallbackTranslations = await fetchTranslations('en');
        }
        if (currentLanguage === 'en') {
            translations = fallbackTranslations;
        } else {
            translations = await fetchTranslations(currentLanguage);
        }
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

    function t(key, params) {
        var value = getNestedValue(translations, key);
        if (value === undefined && currentLanguage !== 'en') {
            value = getNestedValue(fallbackTranslations, key);
        }
        if (value === undefined) {
            return key;
        }
        if (params && typeof value === 'string') {
            Object.keys(params).forEach(function(param) {
                value = value.replace(new RegExp('\\{' + param + '\\}', 'g'), params[param]);
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
        await loadTranslations();
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

    function initPageTranslations(root) {
        var scope = root || document;
        scope.querySelectorAll('[data-i18n]').forEach(function(el) {
            var key = el.getAttribute('data-i18n');
            if (key) {
                var translated = t(key);
                if (translated !== key) el.textContent = translated;
                else warnMissingKey('data-i18n', key);
            }
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
