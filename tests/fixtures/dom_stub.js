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

/**
 * A selector matcher for the subset the tests actually write: a tag name, any
 * number of `.class` steps and any number of `[attr]` / `[attr="value"]`
 * steps, in one compound (no descendant combinators). Enough for
 * `.chip[data-lang]`, `.hero-cat-tile` and `.pack-card-name`; anything richer
 * should be added here rather than worked around in a test.
 */
function matchesSelector(node, selector) {
    const sel = String(selector).trim();
    const re = /^([a-zA-Z][\w-]*)?((?:\.[\w-]+|\[[\w:-]+(?:="[^"]*")?\])*)$/;
    const head = re.exec(sel);
    if (!head) throw new Error('dom_stub: unsupported selector ' + sel);
    if (head[1] && node.tagName !== head[1].toUpperCase()) return false;
    const classes = String(node.className || node.getAttribute('class') || '')
        .split(/\s+/).filter(Boolean);
    const stepRe = /\.([\w-]+)|\[([\w:-]+)(?:="([^"]*)")?\]/g;
    let step;
    while ((step = stepRe.exec(head[2] || '')) !== null) {
        if (step[1]) {
            if (classes.indexOf(step[1]) === -1) return false;
        } else {
            const value = node.getAttribute(step[2]);
            if (value === null) return false;
            if (step[3] !== undefined && value !== step[3]) return false;
        }
    }
    return true;
}

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
    const listeners = Object.create(null);
    let html = '';
    let ownText = '';
    let children = [];

    const el = {
        id: id || '',
        tagName: tagName || 'DIV',
        className: '',
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
        setAttribute: function (name, value) {
            attrs[name] = String(value);
            const data = /^data-(.+)$/.exec(name);
            if (data) {
                el.dataset[data[1].replace(/-([a-z])/g, function (_m, c) {
                    return c.toUpperCase();
                })] = attrs[name];
            }
        },
        getAttribute: function (name) {
            return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
        },
        removeAttribute: function (name) { delete attrs[name]; },
        hasAttribute: function (name) {
            return Object.prototype.hasOwnProperty.call(attrs, name);
        },
        addEventListener: function (type, fn) {
            (listeners[type] || (listeners[type] = [])).push(fn);
        },
        removeEventListener: function (type, fn) {
            const fns = listeners[type];
            if (!fns) return;
            const at = fns.indexOf(fn);
            if (at !== -1) fns.splice(at, 1);
        },
        /**
         * Fire what a real tap fires: the inline ``onclick`` the module may
         * have assigned, then every listener it registered, each with ``this``
         * bound to the element. Tests that need to prove a button still works
         * on the *second* render have to press it on the first one.
         *
         * A disabled control swallows the tap, exactly as the browser does —
         * that silence is what a player experiences when a one-shot button was
         * never re-armed, so a stub that fired anyway would hide the bug.
         */
        dispatch: function (type) {
            if (el.disabled && (type === 'click' || type.indexOf('mouse') === 0)) return;
            const event = {
                type: type,
                target: el,
                currentTarget: el,
                preventDefault: function () {},
                stopPropagation: function () {}
            };
            const inline = el['on' + type];
            if (typeof inline === 'function') inline.call(el, event);
            (listeners[type] || []).slice().forEach(function (fn) {
                fn.call(el, event);
            });
        },
        click: function () { el.dispatch('click'); },
        appendChild: function (child) { children.push(child); all.push(child); return child; },
        // The subtree, depth-first — `children` is reassigned by the innerHTML
        // setter, so it is read through the closure rather than captured.
        querySelectorAll: function (selector) {
            const out = [];
            (function walk(nodes) {
                nodes.forEach(function (node) {
                    if (matchesSelector(node, selector)) out.push(node);
                    if (node.children && node.children.length) walk(node.children);
                });
            })(children);
            return out;
        },
        querySelector: function (selector) {
            return el.querySelectorAll(selector)[0] || null;
        },
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
    scrollTo: function () {},
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
