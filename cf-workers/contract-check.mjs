// Contract check for the worker's pack validator (#256).
//
// The worker (quizify-api.js) is written for the Cloudflare Workers runtime
// (Response.json, fetch, env bindings), so it can't be imported wholesale under
// plain node. Instead we extract the pure `validatePack` function plus the four
// schema constants it closes over, eval them in isolation, and assert that:
//   1. the shared fixture pack (tests/fixtures/community_pack.json) is ACCEPTED
//      (validatePack returns null), exactly as the integration's
//      server/pack_submission.py::validate_pack accepts it; and
//   2. an obviously malformed pack is REJECTED (returns an error string).
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

function fail(msg) {
  console.error(`contract-check FAILED: ${msg}`);
  process.exit(1);
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

// 2. A malformed pack must be rejected. Drop the third answer so the question
//    no longer has exactly ANSWERS_PER_QUESTION answers.
const malformed = JSON.parse(JSON.stringify(fixture));
malformed.questions[0].answers = malformed.questions[0].answers.slice(0, 2);
const badErr = validatePack(malformed);
if (badErr === null) {
  fail('worker accepted a malformed pack (only 2 answers) — schema too loose');
}

console.log('contract-check OK: worker validatePack accepts the fixture and rejects malformed input');
process.exit(0);
