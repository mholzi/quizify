/**
 * Quizify community-pack submission worker (Cloudflare Worker).
 *
 * The Quizify HA integration proxies a validated community pack to this worker
 * (the integration never holds a GitHub token). This worker holds a
 * fine-grained PAT (Issues: Read+Write on mholzi/quizify) as the secret
 * GITHUB_PAT, turns the pack into a GitHub issue, and returns the issue number
 * + URL.
 *
 * Two payload shapes on the SAME endpoint, discriminated by the body's key:
 *
 *   a) submission (#180) — matches server/pack_submission.py::validate_pack
 *      POST { "pack": { name, language, questions: [ { id, question,
 *              answers: [ {text, correct:bool} x3, exactly 1 correct ] } ] } }
 *      → issue labelled `community-pack`
 *
 *   b) request (#579) — matches server/pack_submission.py::validate_request
 *      POST { "request": { theme, language, notes? } }
 *      → issue labelled `pack-request`
 *
 *   Success:  200   { "issue_number": <int>, "issue_url": "<html_url>" }
 *   Error:    4xx/5xx { "code": "INVALID_FORMAT" | "RATE_LIMITED" | "GITHUB_ERROR", "message": "<text>" }
 *
 * The discriminator is the body key, not a new route: the access gate, the
 * method guard and the PAT handling are identical for both, and a second URL
 * would have meant a second wrangler route to keep in sync for no gain.
 *
 * Two ways in (#1127):
 *
 *   1. Authenticated. The caller sends `X-Quizify-Secret` and it matches the
 *      worker's SHARED_SECRET (constant-time compare). This is the self-hosted
 *      path of #292: a host who runs their own worker sets the same secret on
 *      both ends. A header that is present but WRONG is a 401, never a silent
 *      downgrade to the anonymous path.
 *
 *   2. Anonymous. Every install that leaves the integration's URL option empty
 *      talks to the maintainer's worker without a secret, because a secret
 *      shipped in a public repo is not a secret. This path is OFF unless the
 *      deploy opts in: ANONYMOUS_SUBMISSIONS = "on" AND both limiter bindings
 *      (ANON_LIMITER, ANON_COUNTS) are present. Missing any of the three →
 *      401, exactly as before (fail closed, #292). When on, it is held by:
 *        - a per-IP burst limit (ANON_LIMITER, Workers rate-limiting binding),
 *        - a per-IP daily cap and a global daily cap (ANON_COUNTS, KV),
 *        - the same strict payload validation and Markdown/mention escaping
 *          as the authenticated path, fixed labels, and a PAT that can do
 *          nothing but read and write issues on mholzi/quizify.
 *      The caller IP is CF-Connecting-IP, which Cloudflare sets and a client
 *      cannot forge. The integration calls server-side, so that IP is the
 *      host's Home Assistant, i.e. per IP is per install in practice.
 *      The worst an abuser can do is file ANON_DAILY_GLOBAL junk issues a day
 *      on one repo; flipping ANONYMOUS_SUBMISSIONS to "off" stops it.
 *
 * Deploy:  see cf-workers/README.md (KV namespace, secrets, deploy, smoke test).
 *   Secrets: npx wrangler secret put GITHUB_PAT
 *            npx wrangler secret put SHARED_SECRET   (optional since #1127)
 */

const REPO = 'mholzi/quizify';
const GITHUB_ISSUES_API = `https://api.github.com/repos/${REPO}/issues`;

// Mirror the integration's caps (const.py).
const MAX_QUESTIONS = 500;
const MIN_QUESTIONS = 1;
const ANSWERS_PER_QUESTION = 3;
const MAX_BYTES = 1_048_576; // 1 MiB

// Pack-request caps (#579) — mirror REQUEST_MAX_* in const.py.
const MAX_THEME_CHARS = 80;
const MAX_NOTES_CHARS = 500;
const MAX_LANGUAGE_CHARS = 10;

// Anonymous-path budgets (#1127). The burst limit lives in wrangler.toml
// (ANON_LIMITER: a few calls per minute per IP); these are the daily caps kept
// in KV. KV is eventually consistent, so a burst can overshoot a cap by a
// handful — the burst limiter is what bounds that overshoot.
const ANON_DAILY_PER_IP = 5;
const ANON_DAILY_GLOBAL = 40;
const ANON_COUNT_TTL_SECONDS = 2 * 24 * 60 * 60; // keys outlive their day, then go

// CORS (#292): the Quizify HA integration calls this worker server-side (from
// the HA Python backend via aiohttp — server/pack_submission.py), NOT from a
// browser, so no cross-origin browser preflight ever happens. A wildcard
// `Access-Control-Allow-Origin: '*'` only invited browser-based abuse without
// benefiting the one legitimate caller. We therefore drop CORS entirely: no
// ACAO header is emitted and OPTIONS is not treated as a CORS preflight. The
// server-side integration is unaffected (it doesn't rely on CORS). If a browser
// origin ever needs access, set an explicit allow-list here instead of '*'.
const CORS = {};

function jsonError(code, message, status) {
  return Response.json({ code, message }, { status, headers: CORS });
}

/** Constant-time compare of two strings (#292). Encodes both to UTF-8 bytes
 *  and uses crypto.subtle.timingSafeEqual, which requires equal-length buffers
 *  — so we length-guard first (a length mismatch is an immediate non-match and
 *  the length itself is not secret). Returns false on any malformed input. */
function timingSafeEqualStr(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const enc = new TextEncoder();
  const bufA = enc.encode(a);
  const bufB = enc.encode(b);
  if (bufA.byteLength !== bufB.byteLength) return false;
  return crypto.subtle.timingSafeEqual(bufA, bufB);
}

/** Validate the pack against the #179 schema — must match
 *  server/pack_submission.py::validate_pack exactly so a pack the integration
 *  accepts isn't rejected at the last hop, and a direct POST can't push junk. */
function validatePack(pack) {
  if (!pack || typeof pack !== 'object' || Array.isArray(pack)) return 'Top-level JSON must be an object.';
  if (typeof pack.name !== 'string' || !pack.name.trim()) return "Field 'name' is required.";
  const lang = pack.language === undefined ? 'de' : pack.language;
  if (typeof lang !== 'string' || !lang.trim()) return "Field 'language' must be a non-empty string.";
  const qs = pack.questions;
  if (!Array.isArray(qs) || qs.length < MIN_QUESTIONS) return "Field 'questions' must be a non-empty list.";
  if (qs.length > MAX_QUESTIONS) return `Too many questions (max ${MAX_QUESTIONS}).`;
  const seenIds = new Set();
  for (let i = 0; i < qs.length; i++) {
    const q = qs[i];
    const p = `Question ${i + 1}`;
    if (!q || typeof q !== 'object') return `${p}: must be an object.`;
    if (typeof q.id !== 'string' || !q.id.trim()) return `${p}: 'id' is required.`;
    if (seenIds.has(q.id)) return `${p}: duplicate id '${q.id}'.`;
    seenIds.add(q.id);
    if (typeof q.question !== 'string' || !q.question.trim()) return `${p}: 'question' is required.`;
    if (!Array.isArray(q.answers) || q.answers.length !== ANSWERS_PER_QUESTION) {
      return `${p}: exactly ${ANSWERS_PER_QUESTION} answers are required.`;
    }
    let correct = 0;
    for (const a of q.answers) {
      if (!a || typeof a !== 'object') return `${p}: each answer must be an object.`;
      if (typeof a.text !== 'string' || !a.text.trim()) return `${p}: every answer needs non-empty 'text'.`;
      if (a.correct === true) correct += 1;
    }
    if (correct !== 1) return `${p}: exactly 1 answer must be marked correct (got ${correct}).`;
  }
  return null;
}

/** Validate a pack request (#579) — must match
 *  server/pack_submission.py::validate_request. There is no content to check,
 *  only three short fields, so the caps ARE the validation: theme and notes go
 *  verbatim into an issue body. Returns an error string or null. */
function validateRequest(req) {
  if (!req || typeof req !== 'object' || Array.isArray(req)) return 'Top-level JSON must be an object.';
  if (typeof req.theme !== 'string' || !req.theme.trim()) return "Field 'theme' is required.";
  if (req.theme.length > MAX_THEME_CHARS) return `Field 'theme' exceeds ${MAX_THEME_CHARS} characters.`;
  const lang = req.language === undefined ? 'de' : req.language;
  if (typeof lang !== 'string' || !lang.trim()) return "Field 'language' must be a non-empty string.";
  if (lang.length > MAX_LANGUAGE_CHARS) return `Field 'language' exceeds ${MAX_LANGUAGE_CHARS} characters.`;
  const notes = req.notes === undefined || req.notes === null ? '' : req.notes;
  if (typeof notes !== 'string') return "Field 'notes' must be a string when present.";
  if (notes.length > MAX_NOTES_CHARS) return `Field 'notes' exceeds ${MAX_NOTES_CHARS} characters.`;
  return null;
}

/** Neutralise Markdown / mention injection in user-supplied strings going into
 *  the issue: break @mentions and #issue-refs (notification spam / fake
 *  cross-links), escape table/code controls, escape Markdown link/image control
 *  chars so a crafted submission can't plant a clickable link or inline image in
 *  the trusted-looking auto-filed issue (#305), and collapse newlines.
 *  Order matters: escape backslash-family controls first, THEN the link/image
 *  set, so we don't double-escape the backslashes we inserted. */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/[\r\n]+/g, ' ')
    .replace(/[`|\\]/g, '\\$&')
    .replace(/[[\]()!]/g, '\\$&')
    .replace(/@/g, '@​')
    .replace(/#(?=\d)/g, '#​')
    .slice(0, 500);
}

function buildIssue(pack) {
  const qs = pack.questions || [];
  const title = `pack: ${esc(pack.name)} (${esc(pack.language || 'de')}) — ${qs.length} questions`;
  const lines = [
    '## Community pack submission', '',
    'A host submitted a community question pack from the in-app composer.', '',
    '| Field | Value |', '|-------|-------|',
    `| **Name** | ${esc(pack.name)} |`,
    `| **Language** | ${esc(pack.language || 'de')} |`,
    `| **Questions** | ${qs.length} |`,
    '', '### Questions', '',
  ];
  qs.forEach((q, i) => {
    lines.push(`**${i + 1}. ${esc(q.question)}**`);
    (q.answers || []).forEach((a) => {
      lines.push(`- ${a && a.correct === true ? '✅ ' : ''}${esc(a && a.text)}`);
    });
    lines.push('');
  });
  lines.push('---', '*Auto-filed by the Quizify in-app community-pack submission (#180).*');
  return { title: title.slice(0, 250), body: lines.join('\n'), labels: ['community-pack'] };
}

/** Build the issue for a pack REQUEST (#579).
 *
 *  Note `esc()` collapses newlines, so a multi-line note arrives as one line.
 *  That is deliberate: the notes field is a hint, not a document, and a body
 *  that can inject Markdown structure into an auto-filed issue is worse than a
 *  body that reads as one paragraph. */
function buildRequestIssue(req) {
  const theme = esc(req.theme);
  const lang = esc(req.language || 'de');
  const notes = esc(req.notes || '');
  const lines = [
    '## Pack request', '',
    'A host asked for a pack that does not exist yet. Nothing was authored — this is a wish.', '',
    '| Field | Value |', '|-------|-------|',
    `| **Theme** | ${theme} |`,
    `| **Language** | ${lang} |`,
    `| **Notes** | ${notes || '—'} |`,
    '',
    '### What happens next', '',
    '- A generated pack lands as a **pull request** for review — never a direct merge.',
    '- Generator rules that apply (see #579): durable facts over current ones, never let the',
    '  question title carry its own answer, and keep an estimate answer inside its min/max.',
    '- Requests are public: the resulting pack ships to everyone or not at all.',
    '',
    '---', '*Auto-filed by the Quizify in-app pack request (#579).*',
  ];
  return {
    title: `pack request: ${theme} (${lang})`.slice(0, 250),
    body: lines.join('\n'),
    labels: ['pack-request'],
  };
}

/** True when this deploy has opted into the anonymous path (#1127). All three
 *  must be present; a deploy that has only some of them stays fail-closed. */
function anonymousEnabled(env) {
  return (
    env.ANONYMOUS_SUBMISSIONS === 'on' &&
    !!env.ANON_LIMITER &&
    typeof env.ANON_LIMITER.limit === 'function' &&
    !!env.ANON_COUNTS
  );
}

/** Decide how the caller gets in. Returns {anonymous, ip} or {error}.
 *
 *  A presented secret is checked whenever the worker has one, and a mismatch is
 *  a 401 — never a fall-through to the anonymous path, so a self-hosted
 *  integration with a stale secret fails loudly instead of quietly spending the
 *  anonymous budget. A header sent to a worker WITHOUT a SHARED_SECRET carries
 *  nothing to check against and is ignored. */
async function resolveAccess(request, env) {
  const presented = request.headers.get('X-Quizify-Secret');
  if (env.SHARED_SECRET && presented !== null) {
    if (timingSafeEqualStr(presented, env.SHARED_SECRET)) return { anonymous: false, ip: null };
    return { error: jsonError('INVALID_FORMAT', 'Unauthorized.', 401) };
  }
  if (!anonymousEnabled(env)) {
    return { error: jsonError('INVALID_FORMAT', 'Unauthorized.', 401) };
  }
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const { success } = await env.ANON_LIMITER.limit({ key: ip });
  if (!success) {
    return { error: jsonError('RATE_LIMITED', 'Too many requests, slow down.', 429) };
  }
  return { anonymous: true, ip };
}

/** Hex SHA-256 of the caller IP — the KV key never stores the address itself. */
async function ipKey(ip) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(String(ip)));
  return Array.from(new Uint8Array(digest).slice(0, 16), (b) => b.toString(16).padStart(2, '0')).join('');
}

async function readCount(kv, key) {
  const n = parseInt((await kv.get(key)) || '0', 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/** Charge one anonymous issue against the per-IP and the global daily cap.
 *  Returns a 429 response when either is spent, otherwise null. */
async function takeDailyBudget(env, ip) {
  const day = new Date().toISOString().slice(0, 10);
  const ipK = `ip:${await ipKey(ip)}:${day}`;
  const allK = `all:${day}`;
  const [perIp, global] = await Promise.all([
    readCount(env.ANON_COUNTS, ipK),
    readCount(env.ANON_COUNTS, allK),
  ]);
  if (perIp >= ANON_DAILY_PER_IP || global >= ANON_DAILY_GLOBAL) {
    return jsonError('RATE_LIMITED', 'Daily request limit reached, try again tomorrow.', 429);
  }
  const opts = { expirationTtl: ANON_COUNT_TTL_SECONDS };
  await Promise.all([
    env.ANON_COUNTS.put(ipK, String(perIp + 1), opts),
    env.ANON_COUNTS.put(allK, String(global + 1), opts),
  ]);
  return null;
}

async function handleSubmit(request, env) {
  if (!env.GITHUB_PAT) {
    return jsonError('GITHUB_ERROR', 'Worker is missing its GITHUB_PAT secret.', 500);
  }
  // Which way in (#1127): a matching secret, or the opt-in anonymous path.
  const access = await resolveAccess(request, env);
  if (access.error) return access.error;

  const raw = await request.text();
  if (raw.length > MAX_BYTES) return jsonError('INVALID_FORMAT', `Pack exceeds ${MAX_BYTES} bytes.`, 400);
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return jsonError('INVALID_FORMAT', 'Body is not valid JSON.', 400);
  }

  // Discriminate on the body key. A body carrying `request` is a pack request
  // (#579); anything else is treated as a submission, which keeps the old
  // contract byte-for-byte — including the error text for a body with neither
  // key, which still fails through validatePack as it always did.
  let issue;
  if (body && typeof body === 'object' && body.request !== undefined) {
    const reqErr = validateRequest(body.request);
    if (reqErr) return jsonError('INVALID_FORMAT', reqErr, 400);
    issue = buildRequestIssue(body.request);
  } else {
    const pack = body && typeof body === 'object' ? body.pack : null;
    const err = validatePack(pack);
    if (err) return jsonError('INVALID_FORMAT', err, 400);
    issue = buildIssue(pack);
  }
  // Daily caps count only payloads that passed validation, so junk cannot
  // burn the day's budget for real hosts — and junk never reaches GitHub.
  if (access.anonymous) {
    const capped = await takeDailyBudget(env, access.ip);
    if (capped) return capped;
  }
  const ghRes = await fetch(GITHUB_ISSUES_API, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GITHUB_PAT}`,
      Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json',
      'User-Agent': 'Quizify-PackBot',
    },
    body: JSON.stringify(issue),
  });
  if (!ghRes.ok) {
    console.error('GitHub API error:', ghRes.status, await ghRes.text());
    return jsonError('GITHUB_ERROR', `Failed to create issue (HTTP ${ghRes.status}).`, 502);
  }
  const created = await ghRes.json();
  return Response.json({ issue_number: created.number, issue_url: created.html_url }, { headers: CORS });
}

export default {
  async fetch(request, env) {
    // No CORS preflight handling (#292): the integration calls server-side, so
    // OPTIONS/preflight is never needed. Any non-POST method (incl. OPTIONS) is
    // rejected — we don't advertise an open, method-permissive surface.
    if (request.method !== 'POST') return jsonError('INVALID_FORMAT', 'POST only.', 405);
    try {
      return await handleSubmit(request, env);
    } catch (e) {
      console.error('Worker error:', e);
      return jsonError('GITHUB_ERROR', 'Unexpected worker error.', 500);
    }
  },
};
