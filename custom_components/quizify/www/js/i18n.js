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
            var response = await fetch('/quizify/static/i18n/' + langCode + '.json');
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

    var SUPPORTED_LANGUAGES = ['en', 'de'];

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

    function initPageTranslations(root) {
        var scope = root || document;
        scope.querySelectorAll('[data-i18n]').forEach(function(el) {
            var key = el.getAttribute('data-i18n');
            if (key) {
                var translated = t(key);
                if (translated !== key) el.textContent = translated;
            }
        });
        scope.querySelectorAll('[data-i18n-placeholder]').forEach(function(el) {
            var key = el.getAttribute('data-i18n-placeholder');
            if (key) {
                var translated = t(key);
                if (translated !== key) el.placeholder = translated;
            }
        });
        scope.querySelectorAll('[data-i18n-title]').forEach(function(el) {
            var key = el.getAttribute('data-i18n-title');
            if (key) {
                var translated = t(key);
                if (translated !== key) el.title = translated;
            }
        });
        scope.querySelectorAll('[data-i18n-aria-label]').forEach(function(el) {
            var key = el.getAttribute('data-i18n-aria-label');
            if (key) {
                var translated = t(key);
                if (translated !== key) el.setAttribute('aria-label', translated);
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
        return 'en';
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
        init: init,
        isReady: isReady
    };
})();

window.t = window.QuizifyI18n.t;
