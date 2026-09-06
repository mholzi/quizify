/**
 * A DOM small enough to run the player modules under plain node.
 *
 * The player front end is browser JS with no build step and no module system,
 * so the only way a Python test can assert on its *behaviour* — rather than on
 * the shape of its source — is to give it the handful of browser objects it
 * touches and then call the real functions. This is that handful: element
 * lookup by id, class lists, attributes, an innerHTML setter that keeps
 * `data-i18n` children findable, and a `querySelectorAll('[attr]')` good
 * enough for the i18n sweep.
 *
 * Deliberately not a DOM implementation. Anything a test needs that is not
 * here should be added here rather than mocked around, so the next test can
 * see what the previous one relied on.
 *
 * Usage from node:
 *   require('.../dom_stub.js');       // installs global.window / global.document
 *   QZ.el('pl-also');                 // declare an element the page has
 *   QZ.load('.../player-lobby.js');   // run a real player module
 */

'use strict';

const fs = require('fs');

const registry = Object.create(null);
let all = [];

function makeClassList() {
    const set = Object.create(null);
    return {
        add: function () {
            for (let i = 0; i < arguments.length; i++) set[arguments[i]] = true;
        },
        remove: function () {
            for (let i = 0; i < arguments.length; i++) delete set[arguments[i]];
        },
        contains: function (name) { return !!set[name]; },
        toggle: function (name, force) {
            const on = (force === undefined) ? !set[name] : !!force;
            if (on) set[name] = true; else delete set[name];
            return on;
        },
        list: function () { return Object.keys(set); }
    };
}

const SPAN_RE = () => /<span([^>]*)>([\s\S]*?)<\/span>/g;

// Only <span> children are parsed, because the only markup the player modules
// build with innerHTML that the i18n sweep has to see is a row of spans.
function parseSpans(html) {
    const out = [];
    const spanRe = SPAN_RE();
    let m;
    while ((m = spanRe.exec(html)) !== null) {
        const child = makeElement(null, 'SPAN');
        const attrRe = /([a-zA-Z0-9:-]+)="([^"]*)"/g;
        let a;
        while ((a = attrRe.exec(m[1])) !== null) child.setAttribute(a[1], a[2]);
        child.textContent = m[2];
        out.push(child);
    }
    return out;
}

function makeElement(id, tagName) {
    const attrs = Object.create(null);
    let html = '';
    let ownText = '';
    let children = [];

    const el = {
        id: id || '',
        tagName: tagName || 'DIV',
        value: '',
        disabled: false,
        hidden: false,
        dataset: {},
        style: {
            cssText: '',
            setProperty: function () {},
            removeProperty: function () {}
        },
        classList: makeClassList(),
        setAttribute: function (name, value) { attrs[name] = String(value); },
        getAttribute: function (name) {
            return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
        },
        removeAttribute: function (name) { delete attrs[name]; },
        hasAttribute: function (name) {
            return Object.prototype.hasOwnProperty.call(attrs, name);
        },
        addEventListener: function () {},
        removeEventListener: function () {},
        appendChild: function (child) { children.push(child); all.push(child); return child; },
        querySelector: function () { return null; },
        querySelectorAll: function () { return []; },
        closest: function () { return null; },
        children: children
    };

    Object.defineProperty(el, 'innerHTML', {
        get: function () { return html; },
        set: function (value) {
            html = String(value);
            ownText = '';
            all = all.filter(function (node) { return children.indexOf(node) === -1; });
            children = parseSpans(html);
            el.children = children;
            children.forEach(function (child) { all.push(child); });
        }
    });

    // Mirrors the browser closely enough to matter here: reading it walks the
    // subtree, so a sweep that re-translates a child span changes what the
    // parent line says — which is the entire mechanism #809 relies on.
    Object.defineProperty(el, 'textContent', {
        get: function () {
            if (!html) return ownText;
            let i = 0;
            return html
                .replace(SPAN_RE(), function () {
                    const child = children[i++];
                    return child ? child.textContent : '';
                })
                .replace(/<[^>]*>/g, '');
        },
        set: function (value) {
            all = all.filter(function (node) { return children.indexOf(node) === -1; });
            children = [];
            el.children = children;
            html = '';
            ownText = String(value);
        }
    });

    all.push(el);
    return el;
}

const documentStub = {
    documentElement: { lang: 'en' },
    readyState: 'complete',
    title: '',
    getElementById: function (id) {
        return Object.prototype.hasOwnProperty.call(registry, id) ? registry[id] : null;
    },
    querySelector: function () { return null; },
    querySelectorAll: function (selector) {
        const m = /^\[([a-zA-Z0-9:-]+)\]$/.exec(selector);
        if (!m) return [];
        return all.filter(function (node) { return node.hasAttribute(m[1]); });
    },
    createElement: function (tagName) { return makeElement(null, String(tagName).toUpperCase()); },
    addEventListener: function () {}
};
documentStub.body = makeElement('body', 'BODY');

const windowStub = {
    document: documentStub,
    console: console,
    localStorage: {
        _v: Object.create(null),
        getItem: function (k) { return this._v[k] === undefined ? null : this._v[k]; },
        setItem: function (k, v) { this._v[k] = String(v); },
        removeItem: function (k) { delete this._v[k]; }
    },
    navigator: { language: 'en-US' },
    addEventListener: function () {},
    setTimeout: setTimeout,
    clearTimeout: clearTimeout
};

global.window = windowStub;
global.document = documentStub;
// node ships a read-only `navigator`; leave it alone and let the modules that
// care read window.navigator, which is what they do.
windowStub.userAgent = 'node';

const QZ = {
    /** Declare an element the real page has, and return it. */
    el: function (id) {
        if (!registry[id]) registry[id] = makeElement(id);
        return registry[id];
    },
    /** Declare several at once. */
    els: function (ids) {
        return ids.map(function (id) { return QZ.el(id); });
    },
    /** Run one of the www/js modules against this DOM. */
    load: function (file) {
        // indirect eval: the module is an IIFE that assigns onto `window`.
        (0, eval)(fs.readFileSync(file, 'utf8'));
    },
    /**
     * Serve the real i18n bundles to the real i18n.js over global fetch, so a
     * language switch in a test is the same code path as in a browser.
     */
    serveI18n: function (dir) {
        global.fetch = async function (url) {
            const m = /([a-z]{2})\.json/.exec(String(url));
            if (!m) return { ok: false, status: 404 };
            const body = JSON.parse(fs.readFileSync(dir + '/' + m[1] + '.json', 'utf8'));
            return { ok: true, status: 200, json: async function () { return body; } };
        };
    },
    window: windowStub,
    document: documentStub
};

global.QZ = QZ;
module.exports = QZ;
