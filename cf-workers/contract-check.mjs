// Contract check for the worker's pack validator (#256).
//
// The worker (quizify-api.js) is written for the Cloudflare Workers runtime
// (Response.json, fetch, env bindings), so it can't be imported wholesale under
// plain node. Instead we extract the pure `validatePack` function plus the four
// schema constants it closes over, eval them in isolation, and assert that:
//   1. the shared fixture pack (tests/fixtures/community_pack.json) is ACCEPTED
//      (validatePack returns null), exactly as the integration's
//      server/pack_submission.py::validate_pack accepts it; and
//   2. every malformation in the shared catalog
//      (tests/fixtures/community_pack_malformations.json) is REJECTED (returns
//      an error string), in lockstep with the Python validator (#313).
//
// Run: node cf-workers/contract-check.mjs
// Exits 0 on success, non-zero with a message on any failure. Invoked from
// tests/test_worker_contract_256.py so schema drift fails CI on both sides.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, '..');

const workerSrc = readFileSync(join(here, 'quizify-api.js'), 'utf8');
const fixture = JSON.parse(
  readFileSync(join(repoRoot, 'tests', 'fixtures', 'community_pack.json'), 'utf8'),
);
const malformations = JSON.parse(
  readFileSync(
    join(repoRoot, 'tests', 'fixtures', 'community_pack_malformations.json'),
    'utf8',
  ),
).malformations;

function fail(msg) {
  console.error(`contract-check FAILED: ${msg}`);
  process.exit(1);
}

// Apply one shared malformation to a deep copy of the canonical pack. MUST stay
// in lockstep with `_apply_malformation` in tests/test_worker_contract_256.py —
// the same `kind` codes mutate the pack identically on both sides (#256/#313).
function applyMalformation(pack, spec) {
  const p = JSON.parse(JSON.stringify(pack));
  switch (spec.kind) {
    case 'truncate_first_answers':
      p.questions[0].answers = p.questions[0].answers.slice(0, spec.n);
      break;
    case 'delete_field':
      delete p[spec.field];
      break;
    case 'mark_second_answer_correct':
      p.questions[0].answers[1].correct = true;
      break;
    case 'duplicate_first_id':
      p.questions[1].id = p.questions[0].id;
      break;
    case 'blank_first_question_text':
      p.questions[0].question = '   ';
      break;
    default:
      fail(`unknown malformation kind: ${spec.kind}`);
  }
  return p;
}

// Pull the four schema constants out of the worker source so this check can't
// silently use stale values.
function extractConst(name) {
  const m = workerSrc.match(new RegExp(`const ${name}\\s*=\\s*([^;]+);`));
  if (!m) fail(`could not find const ${name} in quizify-api.js`);
  // eslint-disable-next-line no-eval
  return eval(m[1].replace(/_/g, '')); // 1_048_576 -> 1048576
}
const MIN_QUESTIONS = extractConst('MIN_QUESTIONS');
const MAX_QUESTIONS = extractConst('MAX_QUESTIONS');
const ANSWERS_PER_QUESTION = extractConst('ANSWERS_PER_QUESTION');

// Extract the validatePack function definition verbatim.
const fnMatch = workerSrc.match(/function validatePack\(pack\)\s*\{[\s\S]*?\n\}/);
if (!fnMatch) fail('could not extract validatePack from quizify-api.js');

// eslint-disable-next-line no-new-func
const makeValidator = new Function(
  'MIN_QUESTIONS',
  'MAX_QUESTIONS',
  'ANSWERS_PER_QUESTION',
  `${fnMatch[0]}\nreturn validatePack;`,
);
const validatePack = makeValidator(MIN_QUESTIONS, MAX_QUESTIONS, ANSWERS_PER_QUESTION);

// 1. Canonical fixture must be accepted.
const okErr = validatePack(fixture);
if (okErr !== null) {
  fail(`worker rejected the canonical fixture pack: ${okErr}`);
}

// 2. EVERY malformation in the shared catalog must be rejected. The same
//    catalog is replayed on the Python side (test_worker_contract_256.py), so a
//    divergence on either side fails CI (#313).
if (!Array.isArray(malformations) || malformations.length < 3) {
  fail('shared malformation catalog is missing or too small');
}
for (const spec of malformations) {
  const malformed = applyMalformation(fixture, spec);
  const badErr = validatePack(malformed);
  if (badErr === null) {
    fail(
      `worker ACCEPTED a malformed pack ('${spec.id}': ${spec.reason}) — schema too loose`,
    );
  }
}

// ---------------------------------------------------------------------------
// 3. Pack requests (#579) — same idea, second validator.
//
// The request path has no fixture pack to mutate: a request is three short
// fields, so the shared catalog carries whole request objects (valid ones and
// malformed ones) and both sides just run their validator over them.
// tests/test_pack_request_579.py replays the identical file against
// server/pack_submission.py::validate_request.
// ---------------------------------------------------------------------------

const requestCases = JSON.parse(
  readFileSync(join(repoRoot, 'tests', 'fixtures', 'pack_request_cases.json'), 'utf8'),
);

const MAX_THEME_CHARS = extractConst('MAX_THEME_CHARS');
const MAX_NOTES_CHARS = extractConst('MAX_NOTES_CHARS');
const MAX_LANGUAGE_CHARS = extractConst('MAX_LANGUAGE_CHARS');

const reqFnMatch = workerSrc.match(/function validateRequest\(req\)\s*\{[\s\S]*?\n\}/);
if (!reqFnMatch) fail('could not extract validateRequest from quizify-api.js');

// eslint-disable-next-line no-new-func
const makeRequestValidator = new Function(
  'MAX_THEME_CHARS',
  'MAX_NOTES_CHARS',
  'MAX_LANGUAGE_CHARS',
  `${reqFnMatch[0]}\nreturn validateRequest;`,
);
const validateRequest = makeRequestValidator(
  MAX_THEME_CHARS,
  MAX_NOTES_CHARS,
  MAX_LANGUAGE_CHARS,
);

if (!Array.isArray(requestCases.valid) || requestCases.valid.length < 3) {
  fail('shared request catalog has too few valid cases');
}
if (!Array.isArray(requestCases.malformations) || requestCases.malformations.length < 5) {
  fail('shared request catalog has too few malformations');
}
for (const c of requestCases.valid) {
  const err = validateRequest(c.request);
  if (err !== null) {
    fail(`worker REJECTED a valid request ('${c.id}': ${c.reason}) — ${err}`);
  }
}
for (const c of requestCases.malformations) {
  const err = validateRequest(c.request);
  if (err === null) {
    fail(
      `worker ACCEPTED a malformed request ('${c.id}': ${c.reason}) — schema too loose`,
    );
  }
}

console.log(
  `contract-check OK: validatePack accepts the fixture and rejects all ${malformations.length} ` +
    `catalogued malformations; validateRequest accepts all ${requestCases.valid.length} valid ` +
    `requests and rejects all ${requestCases.malformations.length} malformed ones`,
);
process.exit(0);
