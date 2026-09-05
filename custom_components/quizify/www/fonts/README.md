# Bundled fonts

Quizify serves its type from here instead of from Google Fonts and Fontshare
(#737, #738). Two reasons, one change:

- **Privacy.** The README promises "No data leaves your network". Before this,
  every guest phone, the admin page and the television handed their IP address
  and user agent to `fonts.gstatic.com` and `api.fontshare.com` on every load.
- **First paint.** A `<link rel="stylesheet">` to a foreign host blocks
  rendering until it answers *or fails*. On an isolated party network that was
  a white screen at the QR code for as long as the connect timeout ran.

## What ships here

| File | Family | Style | Weights | Subset | Bytes |
|---|---|---|---|---|---|
| `dm-sans-latin.woff2` | DM Sans | normal | 100–1000 (variable) | latin | 62,556 |
| `dm-sans-latin-ext.woff2` | DM Sans | normal | 100–1000 (variable) | latin-ext | 31,312 |
| `dm-sans-italic-latin.woff2` | DM Sans | italic | 400 | latin | 28,400 |
| `dm-sans-italic-latin-ext.woff2` | DM Sans | italic | 400 | latin-ext | 15,172 |
| `jetbrains-mono-latin.woff2` | JetBrains Mono | normal | 400–700 (variable) | latin | 31,340 |
| `jetbrains-mono-latin-ext.woff2` | JetBrains Mono | normal | 400–700 (variable) | latin-ext | 11,596 |

180,376 bytes on disk. A German or English game downloads only the two `latin`
files — 93,896 bytes — which is byte-for-byte what the CDN build already
fetched; the `latin-ext` and italic faces are pulled only when a glyph or a
style actually needs them. woff2 only: every browser this game runs on
supports it, so a second format would be dead weight.

These are Google Fonts' own per-script subsets, copied verbatim from
`fonts.gstatic.com`. They are not subset further — no `pyftsubset` pass, no
weight instancing — because the question packs and player names are free text
and any custom glyph set would eventually be wrong.

## Licences

Both families are **SIL Open Font License 1.1**, which allows bundling and
redistribution inside this MIT-licensed repository as long as the licence
travels with the files. It does:

- `DM_Sans-OFL.txt` — Copyright 2014 The DM Sans Project Authors
- `JetBrains_Mono-OFL.txt` — Copyright 2020 The JetBrains Mono Project Authors

## What is not here, and why

**Cabinet Grotesk** (Indian Type Foundry, via Fontshare) was the display face.
It is published under the *ITF Free Font License*, which Fontshare itself
files under "Closed Source License": free to use, but redistribution of the
font files is not permitted. Vendoring it into an MIT repository would be
exactly that, so it is dropped rather than shipped.

The display role (`--font-display`) now falls to DM Sans at 700–900, which was
already the second entry in that stack. The screens that change are the ones
that lean on the display face: the TV wordmark and question headline, the
podium and champion block, pack card titles and the admin pack-news heading.
Same weights and sizes, a slightly more neutral geometric letterform.
