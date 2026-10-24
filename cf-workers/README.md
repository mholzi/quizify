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
              or  { "request": { theme, language, notes? } }        (pack request, #579)
                  header X-Quizify-Secret: <secret>   (self-hosted workers only; see below)
200               { "issue_number": <int>, "issue_url": "<html_url>" }
4xx/5xx           { "code": "INVALID_FORMAT" | "RATE_LIMITED" | "GITHUB_ERROR", "message": "<text>" }
```

Submissions are labelled `community-pack`, requests `pack-request`.

The pack schema accepted by the worker's `validatePack` is pinned by a shared
fixture, `tests/fixtures/community_pack.json`, asserted valid by **both** the
worker (`validatePack`) and the integration (`server/pack_submission.py::validate_pack`)
in `tests/test_worker_contract_256.py`. If the two schemas drift, that test
fails CI — this is how the original "worker validated the wrong schema" bug
(#256) is kept from recurring.

## Who may call it (#1127)

Since #1127 the integration ships with a default URL,
`https://quizify-api.mholzi.workers.dev` (`DEFAULT_COMMUNITY_SUBMIT_URL` in
`const.py`), so every install can ask for a pack without any setup. Those
installs send **no secret**: a secret committed to a public repo is readable by
anyone, so it would only pretend to protect the endpoint. The worker therefore
has two ways in:

| Caller | How it gets in | Limits |
|---|---|---|
| Self-hosted integration with a secret | `X-Quizify-Secret` matches `SHARED_SECRET` | none beyond validation |
| Default install (no secret) | anonymous path, only if the deploy opted in | 3/min per IP, 5/day per IP, 40/day in total |

The anonymous path is **off** unless the deploy sets `ANONYMOUS_SUBMISSIONS = "on"`
**and** has both the `ANON_LIMITER` rate-limit binding and the `ANON_COUNTS` KV
namespace (all three are in `wrangler.toml`). A deploy missing any of them
answers 401 to callers without a secret, exactly as the #292 fail-closed gate
did. A secret that is present but wrong is always a 401; it never falls back to
the anonymous path.

The trade-off, stated plainly: anyone who finds the URL can file up to 40
issues a day on `mholzi/quizify`, each one validated, escaped (no mentions, no
links, no images) and labelled `community-pack` or `pack-request`. That is the
worst case. The PAT can only read and write issues on this one repo, so nothing a
caller sends can reach code, releases or other repos. If abuse
shows up, set `ANONYMOUS_SUBMISSIONS = "off"` and redeploy; secret-holding
callers are unaffected. The per-IP key is `CF-Connecting-IP`, which Cloudflare
sets and a client cannot forge; because the integration calls server-side, that
IP is the host's Home Assistant, so the per-IP limits are per install in
practice. The integration adds its own per-client limit (5/min) in front.

## Deploy (maintainer, one-time)

```bash
cd cf-workers
npx wrangler login                                  # browser → Cloudflare account mholzi
npx wrangler kv namespace create quizify-anon-counts
#   → paste the printed id into wrangler.toml (ANON_COUNTS), commit it
npx wrangler secret put GITHUB_PAT                  # fine-grained PAT, see "Token" below
npx wrangler deploy                                 # → https://quizify-api.mholzi.workers.dev
```

Smoke test (files one real issue; close it afterwards):

```bash
curl -s -X POST https://quizify-api.mholzi.workers.dev \
  -H 'Content-Type: application/json' \
  -d '{"request":{"theme":"Smoke test, please close","language":"en"}}'
# → {"issue_number":…,"issue_url":"https://github.com/mholzi/quizify/issues/…"}
curl -s -o /dev/null -w '%{http_code}\n' https://quizify-api.mholzi.workers.dev   # → 405 (GET)
```

The new issue must carry the `pack-request` label.

## Self-hosting a worker

Deploy the same file under your own Cloudflare account, then in Home Assistant
→ Quizify integration → **Configure** set **"Community pack submission URL"** to
your worker. The option overrides the default; leaving it empty uses the
default worker again. To lock your worker to your own install, set a shared
secret on both ends:

```bash
cd cf-workers
# generate a random secret, e.g.: openssl rand -hex 24
npx wrangler secret put SHARED_SECRET
npx wrangler deploy
```

and set **"Community pack submission secret"** in the integration options to
the same value. The integration sends the secret only to the URL from the
option, never to the default worker. Set `ANONYMOUS_SUBMISSIONS = "off"` in your
copy of `wrangler.toml` if your worker should accept nothing but your own
install.

## Token

Use a **fine-grained** PAT (github.com → Settings → Developer settings →
Fine-grained tokens):

- Resource owner: `mholzi`; Repository access: **Only select repositories** → `mholzi/quizify`
- Repository permissions: **Issues: Read and write**. Nothing else (GitHub adds
  the mandatory *Metadata: Read-only* on its own).
- Account permissions: none.

If submissions start returning `GITHUB_ERROR`, the token has likely expired or
been rotated; `npx wrangler secret put GITHUB_PAT` again. (This out-of-band
component is a single point of failure — see the Beatify post-mortem; worth a
light uptime check.)
