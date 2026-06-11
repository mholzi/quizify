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
POST /            { "pack": { name, language, questions: [{question, answers[3], correct}], theme? } }
200               { "issue_number": <int>, "issue_url": "<html_url>" }
4xx/5xx           { "code": "INVALID_FORMAT" | "GITHUB_ERROR", "message": "<text>" }
```

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

## Token

Use a **fine-grained** PAT scoped to `mholzi/quizify` with **Issues: Read and
write** only — nothing else. If submissions start returning `GITHUB_ERROR`, the
token has likely expired or been rotated; `npx wrangler secret put GITHUB_PAT`
again. (This out-of-band component is a single point of failure — see the
Beatify post-mortem; worth a light uptime check.)
