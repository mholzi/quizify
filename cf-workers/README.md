# Quizify community-pack submission worker

`quizify-api.js` is the Cloudflare Worker behind the in-app "Bring your own
questions" feature (#180). The Quizify HA integration validates a pasted/composed
pack and proxies it to this worker; the worker holds the GitHub token and files
the pack as an issue in `mholzi/quizify`. The integration never sees the token.

This mirrors the Beatify worker (`beatify-api.mholzi.workers.dev`) but is kept
in-repo here so the source is reviewable (Beatify's worker lives only in the
Cloudflare dashboard, which made a token-drift outage hard to diagnose).

## Contract

```
POST /            { "pack": { name, language, questions: [{id, question, answers[3], correct}], theme? } }
                  header X-Quizify-Secret: <secret>   (only when SHARED_SECRET is set; see below)
200               { "issue_number": <int>, "issue_url": "<html_url>" }
4xx/5xx           { "code": "INVALID_FORMAT" | "GITHUB_ERROR", "message": "<text>" }
```

The pack schema accepted by the worker's `validatePack` is pinned by a shared
fixture, `tests/fixtures/community_pack.json`, asserted valid by **both** the
worker (`validatePack`) and the integration (`server/pack_submission.py::validate_pack`)
in `tests/test_worker_contract_256.py`. If the two schemas drift, that test
fails CI — this is how the original "worker validated the wrong schema" bug
(#256) is kept from recurring.

## Deploy (one-time)

```bash
cd cf-workers
npx wrangler login                      # opens browser → your Cloudflare account
npx wrangler secret put GITHUB_PAT      # paste a fine-grained PAT: Issues Read+Write, repo mholzi/quizify
npx wrangler deploy                     # → https://quizify-api.<account>.workers.dev
```

Then in Home Assistant → Quizify integration → **Configure** → set
**"Community pack submission URL"** to the deployed worker URL. The in-app
submission UI appears once the URL is set (the `/api/quizify/pack-submit/config`
endpoint flips to `enabled: true`).

## Closing the open proxy (`SHARED_SECRET`)

By default the worker is an **unauthenticated** issue-creation proxy: anyone who
learns the URL can POST packs and file (or spam) issues against `mholzi/quizify`,
burning the PAT. To lock it down, set a shared secret on **both** ends — the
worker rejects any request whose `X-Quizify-Secret` header doesn't match:

```bash
cd cf-workers
# generate a random secret, e.g.: openssl rand -hex 24
npx wrangler secret put SHARED_SECRET   # paste the secret
npx wrangler deploy
```

Then in Home Assistant → Quizify integration → **Configure** → set
**"Community pack submission secret"** to the *same* value. The integration
sends it as `X-Quizify-Secret` on every submission.

This is **optional and back-compatible**: with no `SHARED_SECRET` set on the
worker the gate is skipped, and with no secret configured in HA the header is
omitted — existing installs keep working unchanged. Set both, or neither;
setting only the worker secret will reject all submissions until the HA option
matches. (Cloudflare dashboard rate-limiting on the route is still recommended
as defence-in-depth.)

## Token

Use a **fine-grained** PAT scoped to `mholzi/quizify` with **Issues: Read and
write** only — nothing else. If submissions start returning `GITHUB_ERROR`, the
token has likely expired or been rotated; `npx wrangler secret put GITHUB_PAT`
again. (This out-of-band component is a single point of failure — see the
Beatify post-mortem; worth a light uptime check.)
