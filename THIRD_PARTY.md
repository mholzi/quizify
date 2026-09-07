# Third-party material

Quizify itself is MIT (see [`LICENSE`](LICENSE)), but a release ships more than
Quizify's own code: a vendored JavaScript library, two font families and a set
of pack images all travel inside `custom_components/quizify/`. Every one of
them carries a licence whose only real condition is that the licence text stays
with the copy. This file is the index of where each text lives, so that
"it's in the repo somewhere" never has to be the answer.

## Vendored JavaScript

| Path | Upstream | Version | Licence |
|---|---|---|---|
| `custom_components/quizify/www/js/vendor/qrcode.min.js` | [davidshimjs/qrcodejs](https://github.com/davidshimjs/qrcodejs) | commit `04f46c6a0708418cb7b96fc563eacae0fbf77674` | MIT — [`qrcodejs-LICENSE.txt`](custom_components/quizify/www/js/vendor/qrcodejs-LICENSE.txt) |

qrcodejs draws the join QR code on the admin page, the player page and the
television. It has no releases and no tags, so the commit SHA is the version:
`04f46c6a` is upstream master, and has been since 2015-11-25. The vendored file
is byte-identical to upstream below its one-line header comment; `qrcode.min.js`
itself last changed upstream in `06c7a5e` (2013-07-12).

MIT asks for the copyright notice *and* the permission notice to be included in
all copies. Both now sit next to the library in `qrcodejs-LICENSE.txt`, verbatim
from upstream, and the header of `qrcode.min.js` names the copyright holder and
points at that file (#887).

## Fonts

Both bundled families are SIL Open Font License 1.1, and each licence sits next
to the font files it covers:

- `custom_components/quizify/www/fonts/DM_Sans-OFL.txt` — DM Sans
- `custom_components/quizify/www/fonts/JetBrains_Mono-OFL.txt` — JetBrains Mono

Why the fonts are bundled rather than fetched, which subsets ship, and why
Cabinet Grotesk is *not* here: [`fonts/README.md`](custom_components/quizify/www/fonts/README.md).

## Pack images

Each question pack's artwork is credited in a `credits.md` beside it, e.g.
`custom_components/quizify/www/img/packs/music/credits.md`. Those files name the
source and the licence per image; the policy and the test that enforces it are
in [`www/img/packs/LICENSING.md`](custom_components/quizify/www/img/packs/LICENSING.md).
Eleven rows are still on a licence that does not cleanly cover redistribution
outside its country of origin — that is open as #817 and is not resolved here.
