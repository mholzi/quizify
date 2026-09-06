# Changelog

All notable changes to Quizify are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [1.16.0-RC1] — 2026-09-06

Thirty-four entries, chosen in one sitting and built in one — and the biggest of
them were the ones nobody had asked for.

### 🔒 The door was open, and the pictures were not ours

A page open in any browser on the network could reach the game's socket and, in
the right moment, claim the host's seat — lights, speakers and scenes with it.
And a photograph of the Jules Rimet trophy had been shipping in every install
under a licence that requires a credit it never carried; three more pictures were
free in the United States only, two of them tagged on their own source page as
protected in Germany. Four questions are text now, two carry different pictures,
and the check that missed all of it no longer greps for a phrase.

### 📺 Three screens that were quietly three products

The television never followed the game's language, so a German house running an
English quiz framed English questions in German. The phone kept a picture from
the round before. The host's page had no way forward on the last round but a red
button that warned of something it does not do. And the escape hatch for a host
whose phone dies turned out never to have been armed at all.

### 🏗 And underneath

Every game mode now owns its own loop instead of writing one into the socket
handler, the three surfaces share one client and one set of renderers, and a new
guard fails the build when a frame reaches a screen that has no case for it. Team
mode gets its own Hot Seat auction and final wager. A phone's first load went from
1.98 MB to 314 KB.

## [1.15.0] — 2026-09-06

Twenty-four entries, chosen in one sitting and built in one — then played, which
found four more.

### 📺 The room could not read its own result screen

A whole game on real hardware showed the end screen clipping its awards at every
television resolution: at 720p "FASTEST FINGER ·" broke mid-word and the second
award was not on screen at all. The cause was never the television's own
arithmetic — it was a phone stylesheet's `max-width: 160px` reaching the
television and turning a wide pill into a narrow oval. The leaderboard's last row
and the title's descenders came free with it, and the header no longer claims a
sixth question after the fifth.

### 🗣 And an English game spoke German on the phones

Pick English, and every phone showed a German frame around English questions.
The lobby genuinely told them German: only starting a game ever wrote the host's
choice, so a phone joining before that was stamped with the default. The choice
now reaches the lobby, and a change of flag reaches phones already waiting.

### 🔒 The house was open

A guest could claim the free admin slot and drive the host's lights, speakers and scenes
with no Home Assistant credential. Two minutes after the host closed their tab the
persisted token was deleted, and any device on the network could take over.

### 📺 The room can see what happens

Power-ups and reactions reach the television at last: a sentence, not a symbol. The end
screen's awards, the reconnect pill and the estimate reveal all speak the room's language
and its size.

### 🧾 Nothing disappears any more

Your own question packs survive an update. A removed player is told so. A reload no longer
loses the question, and Spanish evenings are narrated in Spanish.

### 🎯 And the scores are right

Two teams of the same name are two teams, a guest who joins between rounds is not a
timeout, and no lightning question is scored after the finale.

### 🔤 The fonts come from your own house

Every page fetched its typefaces from Google and Fontshare on load — against the README's
own promise, and a white join screen for as long as a slow guest network took to answer.
They ship inside the integration now. The display face changes with it: Cabinet Grotesk's
licence forbids bundling, so headlines are set in DM Sans.

## [1.14.0-RC1] — 2026-09-03

Every fix in this release ended somebody's evening.

### 🚪 The game would not start

Over Nabu Casa a whole room counted as one device, so it filled at thirteen players. And a
single malformed community pack took the integration down at setup.

### ⏸️ The pause would not end

A guest who joined during a pause got a fresh full clock, and the room waited at 0:00. One
who reloaded during a host-gone pause lost the reset button.

### 🪑 The hot seat let nobody out

The seat winner could not answer again all game, the round wedged for a host who plays
along, and the television never showed who won the chair.

### 🎛️ And around the game

A dead Stats link, a timer inside a six-pixel bar, a "Final Round!" pill that stayed up, an
analytics page with no bars, and four television strings still sized for a phone.

## [1.13.0] — 2026-09-03

Quizify puts the question on the biggest screen in the room. This release is about
everything that screen was quietly leaving out.

### 📺 Three screens that never fitted a television

The question view, the lightning round and the end screen all ran past the bottom of a
720p picture — the last answers, the percentages, the leaderboard and the head-to-head line
were simply not in it. All three fit now, at 1280×720, 1366×768 and 1920×1080. On short
screens the question is sized by height rather than width, the answers take one column
instead of three, and the awards give way to the ranking. At 1080p nothing changes.

### 🎬 The reveal stops hiding its own numbers

Wrong answers were dimmed as whole tiles, and the distribution chart sits inside those
tiles — so the percentage that says half the room fell for the same one came out at a third
of its contrast. The bar beside it reserved space while invisible, the star was painted over
the number, and the fun fact has moved to where a 720p room can see it.

### 🪑 The hot seat inherits what Lightning already knew

A reload froze the television, the auction's loop outlived its round, and "play again"
switched the auction back on by itself. In team mode both the auction and the final wager
staked a share of a personal score that teams do not keep; both are off for teams now.

---

The first Quizify release since 1.7.0 that was played on a Home Assistant rather than
measured in a browser — and two of the fixes above exist only because that happened.

## [1.13.0-RC3] — 2026-09-03

### ⚡ The lightning round did not fit either

RC2 sized the question and dropped the answers to one column on short screens — and scoped both rules to the question view. The lightning round is the same layout under a different id, and got neither. Played on a television it was worse than the question view ever was: the answers stayed in three 221px columns, each tile 213px tall, and the grid ended 938px down a 720px picture. Twelve elements below the fold, in the phase where the clock is fifteen seconds.

Both views are named on every short-screen rule now. Grid bottom 603px at 1280×720, 631px at 1366×768, nothing below the fold, 1080p unchanged.

A second defect in the same view came out of the same screenshot: the "Get ready!" splash stayed on screen over the running question. The code has always set `hidden` on it — and `hidden` is a UA rule that a class setting `display` beats, so the assignment did nothing at all. A test now checks that trap across every element the dashboard hides by attribute. (#691, #693)

### 🏆 The end screen was hiding the duel

On a 720p television the end of a game showed the podium and two award cards. The leaderboard rows and the head-to-head line — the headline of 1.12.0 — were nine elements below the bottom edge, and had been on every short television since that feature shipped.

Nothing here was too tall; the column simply stacked more than the screen has room for. On short screens the awards now drop their detail line and nothing else changes: that line is the footnote under a heading that already carries the point. Awards 337px → 172px, every leaderboard row in the picture, the head-to-head line at 609–666px of 720. 1080p keeps the detail. (#692, #694)

**Both were found by playing, not by reading.** RC2 was the first build in a month to run on a Home Assistant; these two are what the first two games on it turned up.

## [1.13.0-RC2] — 2026-09-03

### 📺 A long question no longer hides the answers

RC1 was measured in a browser. Played on an actual Home Assistant, round one served a 150-character question and the answers were not on the screen: the question body came to 837 px of the 580 px a 720p television has, twenty elements sat below the fold, and the third answer tile and all three percentages were outside the picture.

The two rules RC1 added both work — they were measured against a 102-character question, which fits in 474 px. The library ships questions up to 173 characters.

So the question is now sized by the axis that is scarce. `4.2vw` sizes it by *width*, and width is the one thing a 1280×720 television has enough of; on short screens it scales with height instead, with a floor, and 1080p keeps the sizes #376 chose for a room. The answers drop to a single column: two columns still wrapped a long answer onto three lines and pushed the chart underneath, 242 px per tile. The full column width takes that to 125 px and keeps the chart on the answer's line.

Measured after, against the heaviest question the library actually ships: 520 px of 573 px at 720p, 527 of 599 at 1366×768, nothing below the fold at any resolution. (#688, #689)

### 📖 The README stopped underselling the library

Three claims had gone stale, all in the same direction. Spanish was described as still missing the World Cup and Estimation packs — both shipped in 1.10.0, and the table directly above the sentence already listed them. The i18n key count said 390 where the bundles carry 666. The language list named German and English, though Spanish has been a full UI language since 1.5.0. (#690)

**What changed since RC1.** This candidate is the first Quizify build verified on a real Home Assistant since 1.7.0 — deployed, restarted, and played through admin, phone and television. The hot seat is still unverified: it fires once per game at random, and the test game ran two rounds.

## [1.13.0-RC1] — 2026-09-03

### 🎬 The reveal stops hiding its own numbers

Wrong answers were dimmed as whole tiles — and the distribution chart sits inside those tiles, so the percentage that says *54 % of us fell for the same one* came out at a third of its contrast. That number only ever appears on a wrong answer. It is the point of the chart.

The bar beside it reserved 140 px of every tile while invisible, which is why answers wrapped into narrow ribbons beside an empty half; at 1280×720 the answer grid spilled 330 px across the leaderboard. And the star marking the correct answer was pinned to the tile's corner, painted on top of that same percentage.

### 📺 The question screen fits a 720p television

It never did. The left column needed 823 px of the 550 px it has, and the overflow came out of *both* ends, so the category label sat behind the timer bar. Short screens now use two answer columns instead of three; at 1080p nothing changes.

The fun fact moves under the leaderboard. It used to begin 6 px below the bottom edge of the picture — nobody with a 720p television has ever seen one.

### 🪑 The hot seat inherits what Lightning already knew

Five defects of one shape: Lightning solved each of these once, and the hot seat was built without inheriting any of it. A reload froze the television (#664). Its background loop outlived the round that started it (#671). "Play again — same settings" switched the auction back on by itself (#670).

In team mode, the auction and the final wager both staked a percentage of a personal score that teams do not keep (#668, #669). Both are switched off for teams now rather than made to look plausible — team play for either is a feature, and Lightning got its team support deliberately.

**Why a release candidate.** None of this has run on a Home Assistant. Every measurement above came from the page rendered in a browser at 1920×1080 and 1280×720 — real pixels, but not a real television, and not a hot seat played by real people.

## [1.12.0] — 2026-08-31

### 🥊 The duel between two regulars

The lobby now names the two people in the room who have played together most often and shows their record: Anna 3 – 2 Ben, last 90 days. The end screen shows it again afterwards, with the game everyone just watched counted in.

A meeting goes to whoever scored higher of those two. A draw counts as a meeting and goes to nobody, and nothing appears until a pair has played twice. Only the shared screens show it; your phone still shows your own standing and nothing else.

### 📺 The lobby fits on the television again

At 1280×720 and 1366×768 the lobby was taller than the picture, and a television does not scroll, so the player names at the bottom were cut off. The new line did not cause this. Short screens now get a smaller join code and tighter spacing; at 1080p nothing changes.

## [1.11.0] — 2026-08-30

### 🪑 The hot seat goes to the highest bidder

Once per game, at a round nobody sees coming, the chair is auctioned. Everyone bids in secret, the highest bid takes the seat and answers one question alone, and the rest of the room may stake points on whether they get it.

A bid is a **share of your own points**, not a number of them. That one choice is what makes the mode work: bid in points and whoever is already ahead wins every auction, so the chair would go to the player who needs it least. In percent everyone can commit everything, and the seat is cheapest for whoever is last.

Right answer wins your stake, wrong answer loses it — and so does no answer at all. Winning the auction means paying for it.

Betting is optional, the player in the chair may not bet, and the auction is off in the kids preset.

### 🎲 An unanswered wager now loses the stake

The same rule reaches back into the final round. Staking points and then letting the clock run out used to cost nothing — no win, no loss. From now on it costs the stake, exactly like a wrong answer.

That forgiveness was deliberate, so a phone locking mid-question never cost anyone points. It goes because the auction above cannot inherit it: a stake that buys the right to answer would be free to anyone who simply sat the question out. One rule for both is easier to play than two that have to be told apart.

The line on your phone that used to promise the opposite has been rewritten in all three languages.

### 🎰 Bet on the category, not on the question in front of you

The final round used to show the question and start its thirty-second clock in the same breath as it asked for your wager. Every second spent deciding was a second taken from answering — and with the rule above, running out of time now forfeits the stake as well.

Betting is its own step now. You get the category and your current points; no question text, no answers, no timer. It closes as soon as everyone has locked in, or after twenty seconds, and only then does the question appear with its full time. The television shows how many bets are in, never how large they are.

It also closes something nobody reported: the question used to be readable while you were betting, so a player who knew the answer could stake everything at no risk. The wager is placed on the category alone now, the way a Jeopardy final is.

## [1.10.0] — 2026-08-23

### 🇪🇸 Spanish is complete

`imagenes-es`, `estimacion-es` and `copa-mundial-es` fill the last three gaps — a picture round, an estimation pack and the World Cup. Every themed pack now exists in all three languages.

**4,740 questions across 36 packs, twelve per language.**

### 👀 See who the room is waiting for

While a question runs, every phone shows a row of players, one chip each, filling in as answers land. The TV shows the count next to the timer.

### 🏅 The season follows you to the end screen

Your all-time standing now appears after the game, not only in the lobby before it — the moment it is actually interesting.

### 🍻 Tonight's score on the TV

Play more than one game and the TV keeps the evening's tally: "Tonight: Anna 2 wins, Ben 1". An evening is a run of games less than six hours apart, so a party past midnight stays one evening.

### ⚡ Power-ups explain themselves

Every power-up now says what it does, in your language, right under the button. Joker removes one wrong answer, Steal takes half of what someone scored this round — no more guessing mid-question.

### 📺 Getting onto the TV, and onto the guests' phones

The lobby shows the join address as text under the QR code, plus "same Wi-Fi as this screen" — for the guest whose phone is on mobile data. "Cast to TV" is now "Open on the TV" and tells you the address to type on the television, which is what it always actually did.

### 🆕 New packs are announced, not homework

The setup screen used to offer version updates for packs you already had and ask you to replace JSON files by hand. Packs ship inside the integration, so that update had already arrived. Now it simply tells you what came with it: *"New in this update: World Cup (100)"*.

### ⏱️ The countdown pulses on the second

In the last five seconds the number beat twice per second against a digit that changes once. Sound and animation now agree about when a second is.

### 🗂️ Host conveniences

Your play history is reachable from the setup screen instead of by guessing the URL. Saving and deleting your own presets happens in the app instead of in grey system dialogs. A host who starts a game without joining now has Next Question and End Game at every reveal. A tap that cannot reach the server says so instead of vanishing.

## [1.9.0] — 2026-08-20

One line in the lobby, a shelf that finally reads the same in three languages, and a way to ask for what is missing.

### 🏅 All-time standing on the join screen

A player who has been here before now sees it while waiting for the game to start: one quiet line under the lobby hero, about the joining player and nobody else ([#371](https://github.com/mholzi/quizify/issues/371)). It names what it counts — "N wins from M games" — so the number is legible without a legend.

**The lobby ranks by wins, the analytics dashboard still ranks by score.** That is a disagreement on purpose. Score-first rewards whoever plays the most, which is the wrong statement for a line meant to start a rivalry across the sofa; the dashboard keeps score-first because that is the right statement for a season overview. A regression test pins the dashboard order so the two never drift into each other by accident.

Ties share a rank, the same competition ranking the in-game leaderboard already uses. A first-time guest sees nothing at all — the line stays hidden until a standing arrives, so nobody reads "1st of 1" on the screen they just joined. A player with games but no win yet gets its own wording rather than a "0 wins" that reads like a taunt.

The standing rides the `joined` and `reconnected` frames, not the roster broadcast, which would ship everyone's history to every phone in the room.

### 🇪🇸 Spanish is finished

Music, food and technology were the last three subjects that existed in German and English but not in Spanish. `musica-es`, `comida-es` and `tecnologia-es` close that gap ([#581](https://github.com/mholzi/quizify/pull/581), [#582](https://github.com/mholzi/quizify/pull/582), [#585](https://github.com/mholzi/quizify/pull/585)) — 160 questions each, picture and estimate rounds included, mirrored on their English siblings rather than translated word for word.

The library now stands at **4,597 questions across 33 packs**, and every themed pack exists in all three languages.

Building them turned up nine outright errors in the new material and about sixty weaker questions, all fixed before merge. Two of the corrections were not in the new packs at all: a shared estimate fact claimed sugar melts before it browns (it is the other way round, ~186 °C versus ~160 °C), and the ten-litres-of-milk-per-kilo rule of thumb undersells a long-aged hard cheese. Both facts are shared by the German, English and Spanish food packs, so they were fixed in all three ([#583](https://github.com/mholzi/quizify/pull/583)) instead of quietly in one.

### 📬 Ask for a pack instead of writing one

If a subject is missing, the host can now request it from inside the app ([#579](https://github.com/mholzi/quizify/issues/579) / [#580](https://github.com/mholzi/quizify/pull/580)) — one endpoint, one store, one rate limit. There is no private path: every request is public, which is the point. Writing a pack by hand stays possible and unchanged.

## [1.9.0-RC1] — 2026-08-17

A player who has been here before now sees it while waiting for the game to start.

### 🏅 All-time standing on the join screen

One quiet line under the lobby hero, about the joining player and nobody else (#371). It names what it counts — "N wins from M games" — so the number is legible without a legend.

**The lobby ranks by wins, the analytics dashboard still ranks by score.** That is a disagreement on purpose. Score-first rewards whoever plays the most, which is the wrong statement for a line meant to start a rivalry across the sofa; the dashboard keeps score-first because that is the right statement for a season overview. A regression test pins the dashboard order so the two never drift into each other by accident.

Ties share a rank, the same competition ranking the in-game leaderboard already uses.

**A first-time guest sees nothing at all.** The line stays hidden until a standing arrives, so nobody reads "1st of 1" on the screen they just joined. A player with games but no win yet gets its own wording rather than a "0 wins" that reads like a taunt.

The standing rides the `joined` and `reconnected` frames — not the roster broadcast, which would ship everyone's history to every phone in the room. It fails soft in all three ways it can be absent: no game state, no analytics wired, or a name with no history.

German, English and Spanish.

### 🧪 Under the hood

Thirteen new tests, suite at **1,831**. `QuizifyGameState.stats_service` is now a public read-only property, so the lobby reads all-time numbers without reaching into a private field across a module boundary.

### 🔭 Not in this candidate

The finale's "new all-time high score!" callout sketched on #371 is independent of this line and was left out. The pack-request flow (#579 / PR #580) is finished and green but was not merged before this tag.

## [1.8.0] — 2026-08-17

One addition changes who is playing. Until now Quizify counted people; from this release it can count sofas. Two people who want to play together say so on their phones, and from that point the room has one fewer participant and one more team.

The rest is content, and it closes a gap that had been open for two releases: Quizify could show a picture and could ask you to guess a number, and almost no pack did either. Now every themed pack does both.

### 👥 Team mode

Players form teams themselves, in the lobby, before the first question ([#365](https://github.com/mholzi/quizify/issues/365)). The host assigns nobody. The lobby asks one question — playing alone, or with someone? — and only a player who answers "with someone" ever sees a team list. Naming a team is optional; leave the field empty and one gets suggested.

**A team is a participant, not a grouping.** It answers once, scores once, and appears in the ranking exactly where a player would. There is no per-capita division and no handicap: four people on one sofa may out-score a pair, and that is the mode working as intended rather than a fairness bug. A player who joins no team keeps their own row next to the teams — a lone player is a team of one, not an error state.

**Any member may change the team's answer until the clock stops.** The answer standing at the buzzer is the team's answer, and the speed bonus keys on the *last* tap, so tapping something instantly and thinking afterwards is not free. A short lock after each change stops two people flipping the answer back and forth in the final seconds. This is deliberately scoped to team mode: a solo player's answer is still final the moment they tap it.

On the question screen the standing answer shows as small member dots on the answer it sits on — no banner. Disagreement shows as dots on two different rows.

**The end-of-game awards go to teams.** Not as a rename — a team records what a player records, so each award gets a reading that follows from the data. Fastest Finger is the time of the tap that *stood*, which means a team that argues to the buzzer genuinely is slower. Buzzkill sums the freezes its members spent, since power-ups are still handed to people.

The Lightning Round works the same way ([#552](https://github.com/mholzi/quizify/issues/552)): one answer, one score, changeable until the clock stops. The television shows the lobby grouped by team, so the room can see who is playing with whom without anyone shouting across the sofa.

### 🖼️ Pictures in every pack

Every themed pack now carries **five image questions** ([#554](https://github.com/mholzi/quizify/issues/554)) — 164 of them across the library, drawn from 50 pictures. Image support has existed since 1.3.0 and, until 1.7.0, no shipped pack had ever used it; even then it was two packs that are nothing *but* pictures. A picture is now something you meet in the middle of an ordinary round.

Sourcing was the entire cost, and it is the reason this took a day rather than an hour. Every licence was verified on the individual file record rather than assumed from the collection — a Ginkgo photograph looked public domain in search results and was CC BY-SA on its own page. Every candidate was then looked at **at 340 px**, the real width of a card on a phone: Bingham's 1912 Machu Picchu photographs are free and, at that size, show some Andean terraces. The Enigma machine, the IBM 350 and Köhler's cacao and coffee plates were dropped for resolution alone, which is why the food pack has no chocolate picture.

Two failure modes recurred often enough to name. **The picture announces its own answer** — a photochrom with `AGRA TAJ MAHAL` printed in the border, Röntgen's plate with the Würzburg stamp, a Maracanã print carrying the date of the final. Each was cropped. **Licence-clean is not ship-clean** — a "Piazilla" watermark in the most convenient Van Gogh scan, an ARQUIVO NACIONAL stamp, negative numbers 53202 and 25194 on the Agence Rol glass plates. Invisible in a thumbnail, obvious on a quiz card.

Music and pop culture were predicted to be licence walls and were: free is essentially what predates the record and studio industries, so the ten pictures there run from 1665 to 1953 and their modern questions stay text. Steamboat Willie is there because it entered the US public domain in 2024 — a copyright expiry, not a trademark one, which is why the question asks about the character rather than treating the image as a logo.

Image questions may also declare `reveal_style: "progressive"` ([#434](https://github.com/mholzi/quizify/issues/434)): the picture opens heavily blurred and sharpens as the round timer drains, lining the reveal up with the speed bonus the scoring already pays. Enabled on the two picture packs; every other pack behaves exactly as before.

### 🎯 A number to guess in every pack

Every themed pack also carries **five estimate questions** ([#566](https://github.com/mholzi/quizify/issues/566)) — 130 of them, built on 50 facts. The slider arrived in 1.4.0 and had spent two releases in two packs of its own.

These were chosen by one rule, and it is worth stating because it shaped every subject: **a shipped pack cannot notice that its own answer has gone stale.** Populations drift, records fall, box office keeps counting, and nobody edits the JSON. So the obvious questions were left out on purpose — the Dead Sea's surface level is the classic geography estimate and it drops about a metre a year; "how many elements are in the periodic table" looks like a constant and gains one every decade or so.

Each subject was mined for something fixed instead. Physical constants in science, one of them fixed by decree — the speed of light stopped being a measurement in 1983 and became the definition of the metre. Rules and dimensions in sport rather than records, which turns out to carry the better stories: the marathon's odd 195 metres exist because a start line moved to Windsor Castle in 1908, and a basketball hoop hangs at ten feet because that was the height of a gymnasium balcony in 1891. Historical hardware in technology, where the intuition is wrong in a consistent direction — the computer that guided Apollo 11 had 4 KB of RAM. Closed works in pop culture: a finished film has a runtime and a finished series has an episode count, and neither will ever change again.

One question was rewritten before it shipped, for the same reason several pictures were cropped: John Cage's silent piece is titled *4'33"*, and that **is** the answer in minutes and seconds. It now describes the piece instead of naming it.

The jalapeño is the deliberate exception to the "pick a firm number" rule. Scoville is a fuzzy scale, judged by tongue long before it was measured, and multiple choice cannot ask it honestly because any two options would both be defensible. A slider can, because closeness scoring rewards the right order of magnitude. That is a use for the mechanic 1.4.0 did not have in view.

### 🌍 Spanish gets all of it

A picture crosses languages unchanged, and so does a number. All six Spanish packs carry the same fifty pictures and the same fifty facts as their German and English siblings, at the cost of question text alone.

The library now stands at **4,117 questions across 30 packs**, 164 of them with a picture and 160 asking for a number. The integration grows by about **4 MB of images**, which every install downloads. Nothing was deleted to make room; the themed packs simply run ten questions longer, and the ceiling moved from 155 to 160.

The two estimate packs and the two picture-round packs are unchanged. Five images in a 15-question pack would be a third of it rather than a garnish, and the picture packs are already nothing but pictures.

### 🧪 Under the hood

The suite stands at **1,818 tests**. Two guards are new, both registries: a pack pair joins by one entry and inherits the whole checklist rather than a copy of it. The image guard covers file existence, both languages carrying the same set, no orphaned weight, and a `reveal_style` declared without an image. The estimate guard covers what can actually go wrong with a slider — an answer outside its own range is *dropped silently* by the loader, so the pack would simply be four questions long with no error anywhere.

One fix worth naming. Sixteen tests read multiple-choice answers off a question drawn from all packs at once — a draw that has been able to serve an estimate question since 1.4.0. They passed because the test seed never happened to serve one, and each new pack pair moved the draw until it did. No game code changed: serving an estimate in a mixed game was always correct behaviour.

### 🙏 Thank you

Every defect in the 1.7.0 line came out of running the thing rather than reading it, and this candidate has not been run anywhere but a build machine. If an evening with it turns up something odd — a picture that never appears, a slider whose range feels wrong, a team score that reads strangely — that report is worth more than another pass over the code.

## [1.8.0-RC1] — 2026-08-12

Release candidate for 1.8.0. The entry below is the release text; the bare `v1.8.0` follows once this has been played on a real install.


Quizify used to count people. From this release it can count sofas: two players
who want to play together say so on their phones, and the room has one fewer
participant and one more team (#365).

Teams are formed by the players themselves, in the lobby, before the first
question — the host assigns nobody. **A team is a participant, not a grouping**:
it answers once, scores once, and stands in the ranking exactly where a player
would. No per-capita division and no handicap, so four people on one sofa may
out-score a pair; a player who joins no team keeps their own row, because a lone
player is a team of one rather than an error state. Any member may change the
team's answer until the clock stops, with the speed bonus keyed to the **last**
tap and a short lock after each change so two people cannot flip it back and
forth at the buzzer. A solo player's answer stays final the moment they tap it.

The end-of-game awards go to teams — not as a rename: a team records what a
player records, so Fastest Finger becomes the time of the tap that *stood*, and
a team that argues to the buzzer genuinely is slower. The Lightning Round works
the same way (#552), which is why a lightning question now runs its full window
in team mode instead of ending when everyone has answered. The television shows
the lobby grouped by team.

Image questions may declare `reveal_style: "progressive"` (#434): the picture
opens heavily blurred and sharpens as the timer drains, riding the countdown
both clients already receive. Enabled on the two picture packs; every other pack
is untouched.

Three cosmetics from a full browser pass (#548): the shareable card printed the
pack *slug* instead of its name, the four `theme: trivia` packs had no icon of
their own, and the image zoom button was anchored to the media box rather than
to the picture.

Four defects were found by *playing* a team game on three phones and a
television, with the suite green throughout. The round **ended on the first
tap**, because a team holding an answer counted as done — so the re-decision the
mode is built on never existed. The **solo guest vanished** from the leaderboard,
the television and the podium once teams became the ranking. The **teammate's
reveal said "no answer given"** for a round their team had answered, since only
the member whose tap stood carried a result. And the **lightning recap showed
nothing but misses**, because it looked the viewer up by player name while the
round had scored the team.

The suite stands at **1,386 tests**. The library is unchanged at 30 packs /
3,857 questions.

## [1.7.0] — 2026-08-11

The final 1.7.0 release — the culmination of release candidates RC1–RC8. See
the GitHub release notes for the consolidated highlights; the RC entries below
carry the detailed per-candidate history.

Two of the three additions are about the host rather than the players. Hosting
no longer means leaving the dashboard for the admin page: `custom:quizify-host-card`
puts start / advance / end on a Lovelace view in two densities (#278). And the
setup screen no longer has to be re-done every session — packs, difficulty,
rounds, timer and lightning can be saved by name and re-applied with one tap,
server-side so a preset saved on the tablet is there on the phone (#433).

The third is content: **Bilderrätsel / Picture Round**, 17 image questions in
German and English (#537), the first shipped pack to use the image support
Quizify has carried since 1.3.0 — made possible by teaching `image_url` to
point at pictures shipped inside the integration instead of a third party's
server (#536). Every licence was verified on the object record; two candidates
were dropped for failing that check and four for being unrecognisable at quiz
size, which is why the pack holds 17 questions rather than the 20 the ticket
sketched. The library now stands at **30 packs / 3,857 questions**.

Every fix below came out of running the game on a real install rather than
re-reading the tests, which stayed green throughout. The picture round shipped
**invisible** — the server had learned to accept local image paths but the
dashboard, the player and the lightning round each kept their own
`^https?://`-only check and discarded them (#540). A 5-round Easy game on the
new pack then **ended after round 2**, because the auto Lightning Round claimed
the last of the easy questions out of the main queue (#544). The end screen was
**throwing** on its host-button gate, which left both that button and the
"wait for the host" note hidden (#546). Home Assistant's loop watcher was
flagging a blocking directory scan on the first player render (#542). And the
compact host card could not end a game (#278), while a preset chip and the mode
cards could disagree about what was selected (#433).

### Internal

- Test suite at **1,376 passing** (1,315 in 1.6.1).

## [1.7.0-RC8] — 2026-08-11

Eighth release candidate for 1.7.0. Found by playing the picture round to the
end instead of only checking that its first question rendered.

### Fixed

- **The auto Lightning Round could strand the main game (#544).** A 5-round
  Easy game on the picture pack ended after round 2. The pack ships 7 easy
  questions of 17; two rounds took two, and the lightning detour claimed the
  remaining five out of the main queue — leaving nothing for rounds 3–5. With
  a fixed difficulty there is no fallback rung, so the game simply ended, with
  nothing in the UI to explain it. The "With kids" preset (5 rounds, Easy) hit
  the same wall.

  Lightning now leaves the main game the questions it still owes
  (`total_rounds - round`) and skips itself when nothing is spare — a missing
  bonus round is better than a game that stops three rounds early. Questions
  the main queue is not holding stay free, so a full-size pack is unaffected.

## [1.7.0-RC7] — 2026-08-11

Seventh release candidate for 1.7.0. Housekeeping, found while verifying RC6 on
a real install.

### Fixed

- **Blocking `scandir` on the event loop (#542).** The player's UI-language
  chips derive their set from the shipped `www/i18n/*.json` bundles, and that
  directory scan ran inside the loop on the first player render after every
  start — enough for Home Assistant's loop watcher to flag the integration and
  ask for a bug report.

  The set is `lru_cache`d, so this cost one blocking scan per HA start rather
  than one per request. Setup now primes the cache in an executor, the same
  treatment #343 gave this file's manifest and HTML reads; the language chips
  arrived later (#492) and had missed the pattern.

## [1.7.0-RC6] — 2026-08-11

Sixth release candidate for 1.7.0. One fix, and it is the one that makes RC5's
headline feature actually visible.

### Fixed

- **Picture questions rendered without the picture (#540).** Every question in
  the packs from #537 arrived with a `/quizify/static/…` image URL that the
  browser then discarded: the dashboard, the player and the lightning round
  each carried their own `^https?://`-only check, so the local form #536 had
  just taught the server to accept was dropped on the client. Both the TV and
  the phone showed the question text with no image.

  The rule now lives once, in `QuizifyUtils.safeImageUrl()`, mirroring the
  server's `_sanitize_image_url` — absolute `http(s)`, or a path under
  `/quizify/static/` with no traversal — and the three render sites call it.
  Found by playing a round rather than by re-reading the server tests, which
  were green throughout.

## [1.7.0-RC5] — 2026-08-11

Fifth release candidate for 1.7.0. Content, not code.

### Added

- **A picture round (#537).** "Bilderrätsel" and "Picture Round", 17 image
  questions in German and English behind the same pictures — paintings and
  objects from The Met's open-access collection (CC0) and two NASA photographs
  (public domain). Image questions have been supported since v1.3.0 and no
  shipped pack had ever used one; this gives the feature content.

  Every image's licence was verified on the object record rather than assumed
  from the collection — two candidates were dropped for failing that check —
  and `credits.md` next to the images records the source and how each was
  verified. Attribution is not required by CC0 or public domain; the file
  exists so the provenance stays checkable.

- **`image_url` may point at images shipped with the integration (#536).**
  Paths under `/quizify/static/` are accepted alongside absolute `http(s)`
  URLs, so a pack can carry its own pictures instead of depending on a third
  party's server. Traversal and every other form stay rejected.

The library now stands at **30 packs / 3,857 questions**.

## [1.7.0-RC4] — 2026-08-11

Fourth release candidate for 1.7.0. Two display fixes on RC3's saved presets,
both found by driving the admin page on a real install rather than by the
test suite.

### Fixed

- **A preset chip stayed highlighted after the settings changed (#433).** The
  chips were drawn on load, save and apply; the mode cards were repainted on
  every change. Two paints, one question — they are now repainted together.
- **No mode card was highlighted while a saved preset matched (#433).** A
  saved preset has no card of its own, and the "Eigene" fallback was skipped
  because a match existed, so the card row showed nothing selected. A saved
  preset now lights "Eigene", which is what it is relative to the four
  built-in modes.

Neither defect lost or mis-saved anything; the settings themselves always
round-tripped correctly.

## [1.7.0-RC3] — 2026-08-11

Third release candidate for 1.7.0. One feature.

### Added

- **Saved game presets (#433).** Setup spans packs, difficulty, rounds, timer
  and lightning, and most hosts reconfigure the same two or three combinations
  every session. They can now be saved by name and re-applied with one tap,
  from a scrollable chip row above the four built-in mode cards — which are
  unchanged.

  Presets live on the server (`presets.json`, alongside the other small state
  files) rather than in the browser, so one saved on the living-room tablet is
  there on the host's phone too. Reading and writing them goes through the
  admin-token gate; a corrupt file degrades to "no presets" rather than
  blocking setup. Up to 20 presets, names up to 40 characters.

  A preset stores rounds, difficulty, timer, lightning and the pack selection —
  deliberately not the TTS or House Plays Along settings, which belong to
  devices rather than to the shape of an evening.

## [1.7.0-RC2] — 2026-08-10

Second release candidate for 1.7.0. One fix, found by putting RC1's card
through every game phase rather than by reading the code again.

### Fixed

- **The compact host card can end a game (#278).** Its footer shipped with the
  join link and the status line but without the end action the spec calls for,
  so a host could start a game and advance it through every phase yet had to
  switch to the cockpit or the admin page to finish it. The action now sits
  beside the join link — as text, not a second solid button, so it does not
  compete with the primary control — and appears only where it can act: not in
  `mode: expanded`, whose control row already carries it, and not in the lobby,
  where there is no game to end.

## [1.7.0-RC1] — 2026-08-10

First release candidate for 1.7.0. One feature, cut early so it can be tested
on real hardware before the final release rather than after it.

### Added

- **Host controls as a Lovelace card (#278).** `custom:quizify-host-card` puts
  start / advance / end on a dashboard, so hosting no longer means leaving the
  dashboard for the admin page. One resource with two densities: `mode: compact`
  (the default) is a single button that changes with the game phase, sized for
  one thumb; `mode: expanded` adds the player roster with connection dots, the
  join QR, round progress, and the reveal / end / reset row, for a tablet that
  does nothing else. Placing the card twice with different modes covers the
  "status here, controls there" case without a second resource.

  It speaks the existing admin WebSocket — `admin_auth` first, then
  `admin_connect`, rendering from `game_state` — so there is no new server
  surface, and the phase-to-action mapping mirrors `admin.js` rather than
  inventing transitions. Because Quizify is served from the same origin as the
  Home Assistant frontend, the card reuses the admin token the admin page
  already stored. That token only exists once `/quizify/admin` has been opened
  on that browser, so a dashboard that never has shows an explicit link instead
  of dead buttons.

  The card registers itself as a Lovelace resource when Lovelace runs in storage
  mode; anything else (YAML mode, an unexpected core shape) logs the manual
  step instead of failing setup.

### Notes

- The TV card sketched in the same issue was **not** built. Its only real
  justification was HA Cast, and `/quizify/dashboard` already serves that view.
- Documentation was refreshed for 1.6.1 in the same window (#531): the README's
  library figures were four minors out of date, and `docs/release-notes/` was
  never actually tracked by git.

## [1.6.1] — 2026-08-09

The final 1.6.1 release — "The Room Stops Giving It Away", the culmination of
release candidates RC1–RC5. See the GitHub release notes for the consolidated
highlights; the RC entries below carry the detailed per-candidate history.

This is mostly the bill for 1.6.0. Handing the game to the whole house put
three things in front of real guests that testing never saw: the television
had been rendering answers in question-file order, so on 16 of the 26 shipped
packs the correct answer sat on tile A for every question of every game
(#521); the setup screen's entity pickers came up empty for anyone reaching
Home Assistant remotely (#524, #527); and a host who closed their admin tab
could be locked out of it permanently, because the browser kept a lasting
credential in `sessionStorage` (#530). Alongside those, the "With kids" preset
stopped leaving the auto Lightning Round armed (#513), and the setup screen
stopped asking for a second speaker it did not need (#525).

Two entries are additions rather than repairs: shareable end-of-game result
cards on the end screen (#369), and two new Spanish packs, `deportes-es`
(#515) and `cultura-pop-es` (#517), 150 questions each, which take Spanish to
6 packs / 900 questions and the library to 28 packs / 3,823 questions. Three
incorrect fun facts in the latter were corrected before release (#519).

### Internal

- Test suite at **1,315 passing** (1,084 in 1.6.0).

## [1.6.1-RC5] — 2026-08-05

Fifth release candidate for 1.6.1. One fix, reported from a live host: the
setup screen's entity dropdowns were empty, and the reason turned out to have
nothing to do with the dropdowns.

### Fixed

- **The host could be locked out of their own admin page, permanently.** The
  admin session token is a lasting credential — the server writes it to disk
  and only ever issues a new one when no token exists at all. The browser,
  though, kept its copy in `sessionStorage`, which is discarded the moment the
  tab closes. So once a host closed the admin tab (or restarted Home Assistant
  while it was closed), the credential was orphaned: no browser could present
  it, and no browser could earn a replacement. Every admin tab opened after
  that was refused, silently and for good.

  What that looked like in practice was not an error message but an empty
  setup screen: the rejected connection is the one that carries the lists of
  TTS engines, speakers, lights and scenes, so the dropdowns in steps 7 and 8
  stayed empty and offered nothing to choose. A host with 72 lights saw none
  of them.

  The token now lives in `localStorage`, which is the lifetime the server
  already assumed it had. An admin tab that is still open keeps working —
  its old per-tab token is carried over on first read, not discarded.

  If you are locked out right now, the `quizify.reset_admin_session` service
  clears the stranded token and the next admin page you open claims a fresh
  one.

## [1.6.1-RC4] — 2026-07-30

Fourth release candidate for 1.6.1. One change: the setup screen stops asking
the same question twice.

### Changed

- **One speaker for the whole game (#525).** Step 7 asked for a speaker for the
  narration and step 8 asked again for the sound effects. Both were labelled
  "Speaker", both offered the same devices, and nothing on screen said they were
  related — so every host paid that confusion in order that the few who want
  their narration and their effects on different speakers could have it.

  Now the game asks once. The split is still available, but it moved inside
  step 8's "Customise single effects" panel, where an empty setting means
  "follow the game speaker".

  If you had already set two different speakers, nothing is quietly reassigned:
  the second one is kept and that panel opens by itself the next time you load
  the setup screen, so you can see the split and decide whether you still want
  it. A second setting that merely repeated the first is treated as what it
  was — not a choice — and simply follows along from now on.

  The speaker control also sits outside the narration group now, so it stays
  usable when you switch the quizmaster voice off but still want the house to
  react.

## [1.6.1-RC3] — 2026-07-30

Third release candidate for 1.6.1. One change, reported from a live host and
traced to a race that had been hiding in plain sight since the entity pickers
were built.

### Fixed

- **The setup screen's entity pickers no longer come up empty (#524, #527).**
  On every fresh admin tab the page asked the server for its TTS engines,
  speakers, lights and scenes *before* it had the token needed to ask — so the
  request was refused, and the refusal then painted "None found — configure in
  HA" over the lists the WebSocket had already delivered a moment earlier. The
  pickers were correct, briefly, and then wiped.

  Hosts reaching Home Assistant remotely hit this every time: the socket is
  already open and answers instantly, while the doomed HTTP request takes the
  long way round and lands last. On a local network the two arrive in either
  order, which is why it never showed up in testing. The reporting host had two
  TTS engines, 23 media players and 72 lights, and the screen insisted there
  were none.

  The request that cannot succeed is gone, and no failed load may overwrite a
  successful one any more — a request that was refused learned nothing about
  your system and has no business overruling one that did. This also restores
  the party-light picker in "Does the house play along?", which was blanked by
  the same mechanism.

- **The narration toggles in "Quizmaster voice?" are spaced properly (#526).**
  Their labels sat flush against their switches while the surrounding steps
  looked right. The gap now lives on the shared toggle style instead of being
  re-declared by each panel, which is how one panel came to be missed.

## [1.6.1-RC2] — 2026-07-29

Second release candidate for 1.6.1. One change since RC1, and it is the first
thing Quizify has ever shipped that is aimed outward rather than at the game
itself.

### Added

- **Shareable end-of-game result cards (#369).** The end screen now carries a
  score slip — rank, packs, hit rate, points, and a strip of one glyph per
  round — with a "Share card" button behind it. The text goes to
  `navigator.share`, falling back to the clipboard: the OS share sheet needs
  HTTPS and a user gesture, and plenty of Home Assistant installs run over
  plain HTTP, so the fallback is a real path rather than a courtesy. Dismissing
  the share sheet reports nothing, because claiming "copied" there would be
  false.

  The card is a public claim once it lands in a group chat, so it refuses to
  flatter: a timeout keeps its own glyph instead of being folded into a wrong
  answer, a player who joined late reports only the rounds they actually
  played, tied players share a rank, and a player who never answered a round
  gets no card at all. Power-ups appear as a count rather than a mark on a
  specific round — the game does not record which round one was spent in, and
  placing it would be invented data.

  Nothing new is computed for this: `PlayerSession.round_history` has recorded
  correct / wrong / timeout per round all along. The payload rides the finale
  message, which is sent once per game, so the round-summary path is untouched.
  Full de / en / es.

### Internal

- Test suite at **1,205 passing**. `test_share_cards_369.py` adds 14 tests that
  pin the honesty rules above, rank ordering, pack pass-through, and that the
  serialized results are a copy rather than live game state.

## [1.6.1-RC1] — 2026-07-27

Release candidate for 1.6.1 — "Tile A". Five changes since 1.6.0, one of which
is visible from the sofa: the television had been showing the correct answer.

### Fixed

- **The TV/cast grid rendered answers in question-file order (#521).** Quizify
  shuffles answers per round and again per phone, but the payload the big
  screen renders was built straight off `question.answers`. In **16 of the 26
  shipped packs** the correct answer sits at index 0 of every question, so on
  those packs it was tile A for every question of every game. Nobody was
  mis-scored — the phones always had a real per-player shuffle — but it leaked
  to everyone watching the television. The same cause made the narrator speak
  the round shuffle while the grid drew file order, so a spoken "B" pointed at
  a different tile; the comment above that call had asserted the opposite. The
  fix threads the existing `shuffle_map` through the admin/dashboard payload,
  the reveal highlight, the #151 distribution bars and both reconnect
  snapshots, with a fallback to file order when the map is unusable — a
  mislabelled grid is worse than an unshuffled one. Reordering the pack JSONs
  would have hidden the leak while leaving the narration and reconnect paths
  broken, so the packs are untouched.
- **The "With kids" preset left the auto Lightning Round armed (#513).** The
  preset bundles 5 rounds / easy / 180 s because an external host reported that
  small children cannot read a question and four answers in 45 s. It never
  touched the lightning toggle, so those hosts got an ambush round of five
  questions at 15 s each — the one setting they had asked for, overridden
  mid-game. Selecting the preset now switches the Lightning Round off; the
  other presets keep it, and re-enabling it by hand makes the run "Custom".
- **Three incorrect fun facts in `cultura-pop-es` (#519).** Questions and
  answers were correct; the trivia line was not (Buzz's working title under a
  Woody question, four Marvel film series counted as five, *Jurassic Park*'s
  six minutes of CGI described as total screen time). The fun fact is read
  aloud after the reveal — with narration enabled the speaker states it to the
  room as fact, so a wrong one is worse than none.

### Added

- **Spanish sport pack `deportes-es` (#515)** and **Spanish pop culture pack
  `cultura-pop-es` (#517)**, 150 questions each, 48 easy / 66 medium / 36 hard.
  Neither is a translated English pack. Spanish now has 6 packs / 900
  questions; the library stands at 28 packs / 3,823 questions. Still missing in
  Spanish: music, food, technology.

### Internal

- Test suite at **1,191 passing** (1,084 in 1.6.0). `test_tv_grid_shuffle_521.py`
  adds eleven tests covering the payload order, narrator/grid agreement, both
  snapshot paths, the distribution mapping and the malformed-map fallback.
  `test_reconnect_snapshot_projection_253.py` had pinned the leaking file-order
  contract as correct and was rewritten against the shuffled one.

## [1.6.0] — 2026-07-22

The final 1.6.0 release — "The House Plays Along", the culmination of release
candidates RC1–RC6. See the GitHub release notes for the consolidated
highlights; the RC entries below carry the detailed per-candidate history.

Two threads run through this release. The house joins the game: a `quizify_*`
event backbone on the HA bus, light choreography with a countdown pulse, room
sound effects with four bundled cues, a finale scene selector, and a setup
panel with three one-tap presets — all off by default. And the room becomes
playable for everyone in it: a reveal that no longer depends on colour, an
opt-in comfort mode, a per-player interface language, and a 180-second timer
with a "With kids" preset.

Alongside those, five HA services expose the host controls to voice and
automations, and question selection now weighs against what players have
recently seen.

### Internal

- **Repository licence and HACS validation (#512).** The README had advertised
  MIT since launch in three places, including a badge linking to a `LICENSE`
  file that never existed — the project presented itself as MIT while carrying
  no effective licence. The file now exists. Alongside it, the `hacs/action`
  and `hassfest` workflows Quizify had been shipping without now run on every
  push and nightly; the first run found both the missing licence and an
  illegal `description` key in `manifest.json`, which hassfest rejects
  outright. Both are fixed and all nine HACS checks pass.

## [1.6.0-RC6] — 2026-07-22

Release candidate for 1.6.0 — "The House Plays Along". One change: mixed-
language households get a language each, instead of the host's.

### Added

- **Per-player UI language picker (#492)** — flag chips above the name field
  on the join screen let each phone pick its own chrome language, remembered
  per device. The questions stay in the host's pack language, so this
  translates the labels around the game rather than the game itself; the row
  is styled as a quiet setting to match.

  The chips list the languages the *interface* is translated into, which is
  not the same set as the languages the host has *packs* for — on a
  German-only install those differ, and the difference is exactly the person
  this is for: the Spanish-speaking guest at a German host's party.

### Fixed

- **The host's language no longer overrides a player's choice (#492)** — every
  `game_state` broadcast carried the host's game language and the player
  applied it unconditionally, so a picker without this guard would have been
  overwritten within milliseconds of being used. The sync now yields whenever
  the player has chosen for themselves, and still applies for everyone who
  hasn't.

## [1.6.0-RC5] — 2026-07-22

Release candidate for 1.6.0 — "The House Plays Along". One change: the reveal
stops depending on colour, and the player screen gains a comfort mode.

### Added

- **Accessibility mode (#372)** — an opt-in toggle beside the sound speaker in
  the player header, persisted per phone, that enlarges the type and holds
  motion still. `prefers-reduced-motion` was already honoured globally, so this
  is the manual override for a phone whose owner never set the OS switch — a
  kid's device, a guest's hand-me-down. The class is applied from the document
  head rather than from the player bundle, because the mode changes the root
  font size and applying it after first paint re-flows the whole screen on
  every load.

### Fixed

- **The reveal no longer depends on colour alone (#372)** — DESIGN.md has
  always required that "correct/incorrect [is] always paired with glyph
  (★ / ×)". The TV dashboard honoured it; the player's answer buttons never
  did — after a reveal, right and wrong differed only in border and background
  hue, and at Soft Parlor saturations sage (#7FA897) and dusty red (#D66A6A)
  collapse into near-identical grey-greens under red-green colour blindness. A
  colour-blind guest could not read their own result. The buttons now carry the
  same two glyphs the TV uses. Unconditional, not behind the new toggle: a
  guest on a borrowed phone never finds a settings switch.
- **Header toggles announced their translation key (#372)** — the sound and
  accessibility buttons set `aria-label` from JS, so `initPageTranslations()`
  never touched them, and i18n resolves after `init()` runs. A screen reader
  read out "game dot sound mute". Both labels are now repainted once
  translations land, mirroring the existing page-title repair.

## [1.6.0-RC4] — 2026-07-21

Release candidate for 1.6.0 — "The House Plays Along". Completes the house
feature with its setup panel and the audio it was missing, and gives hosts
playing with small children a timer they can actually read a question in.

### Added

- **House Plays Along setup panel (#494)** — the light choreography, room SFX
  and event backbone from RC2 were reachable only through a single config-flow
  boolean. The admin setup now has a panel of its own (mirroring the narration
  panel from #281): three one-tap presets — Game Show, Cozy Glow, Events only —
  plus an advanced section with per-effect toggles and entity pickers for the
  party lights, the SFX speaker and the winner scene. Presets resolve to plain
  booleans before they reach the backend, and the runtime survives an
  options reload.
- **The house finally makes a sound (#494)** — phase 3 shipped the sound-effect
  consumer but no audio, so every cue silently no-op'd unless the host went
  hunting for CC0 files. Four default cues (correct, wrong, streak, winner) now
  ship with the integration, with the per-cue URL override still available for
  hosts who want their own.
- **A 180 s per-question timer (#506)** — an external host playing with small
  children reported they cannot read a question plus four answers in time even
  at 45 s, which was the highest the picker offered. The ceiling was purely a
  front-end one — the backend has always accepted 5–300 s — so the picker now
  offers 20 / 30 / 45 / 180 s.
- **A "With kids" preset (#506)** — 5 rounds · Easy · 180 s, sitting between
  Classic and Marathon so the cards stay ordered by session length. The timer
  picker lives behind *Custom settings*; a host who starts from a preset card
  would never have found the new option without this.

### Internal

- **Test suite: sockets are enabled explicitly (#508)** — the pytest lane went
  red without a code change when the GitHub runner image moved from 20260705 to
  20260714: 23 failures, all `SocketBlockedError`, in the tests that spin a real
  aiohttp `TestServer`. Re-running the last green build on its original commit
  reproduced it exactly, so the cause was environmental. The suite used to
  attach pytest-socket's `enable_socket` *marker* to every collected item, which
  only works if pytest-socket's setup hook wins an ordering race we do not
  control. Sockets are now enabled explicitly from a `trylast` setup hook and an
  autouse fixture.

## [1.6.0-RC3] — 2026-07-07

Release candidate for 1.6.0 — "The House Plays Along". Fixes the admin
narration-entity dropdowns, which stayed empty on RC2.

### Fixed

- **Admin TTS/media-player dropdowns empty (#502)** — the engine + speaker
  pickers in the narration panel (#281) populated from the admin-token-gated
  `/api/quizify/tts-entities` endpoint (#356), whose fetch fired at page-init
  before the admin token arrived over the WebSocket, so it 401'd and the
  dropdowns fell back to "None found" (the RC2 refetch band-aid in #501 didn't
  reliably close the race). The admin-connect WebSocket frame — already
  admin-authenticated — now carries the TTS-engine and media-player lists
  directly, so the dropdowns populate from a trusted channel with no separate
  token-gated fetch and no race. The HTTP endpoint stays as a fallback.

## [1.6.0-RC2] — 2026-07-06

Release candidate for 1.6.0 — "The House Plays Along". The whole smart home
becomes part of the game show, question selection stays fresh across game
nights, and hosts can drive the game hands-free via HA services.

### Added

- **The House Plays Along (#494)** — an immersive whole-home game-show mode,
  off by default behind the `house_events_enabled` master toggle. Three
  layers ride a new `quizify_*` HA event backbone that fires at every game
  milestone: (1) the event backbone itself (#366/#495) so hosts can wire
  their own automations; (2) "Game Show" light choreography (#496) — party
  lights react to game beats with transient accents; (3) room sound effects
  (#497) at reveal, streak and winner, each cue resolvable from an override
  URL or a bundled default.
- **Countdown pulse + finale scene (#280)** — the party lights "breathe"
  faster in the final seconds of a question (new additive
  `quizify_time_running_out` event), and an options-flow **finale scene
  selector** fires a user-built `scene.*` when the winner is decided.
- **Freshness engine (#436)** — question selection now weights against
  recently shown questions (exponential decay + a hard-exclude window with a
  pool-size guard) so repeat game nights feel new. New "Avoid recent repeats"
  options toggle (on by default; off restores the previous ordering exactly).
- **Game-control HA services (#367)** — `quizify.start_game`,
  `quizify.next_round`, `quizify.pause`, `quizify.resume` and
  `quizify.end_game` expose the host controls as HA services, so the game can
  be driven from Assist voice ("Hey Nabu, next question"), a Zigbee remote, a
  dashboard button or an automation. Bad-phase calls raise a clear
  `ServiceValidationError`.

### Fixed

- **TTS engine + speaker dropdowns showed "None found" (RC2).** The admin
  quiz-setup dropdowns load from the admin-token-gated `tts-entities` endpoint
  (#356) at page-init, before the admin session token arrives over the
  WebSocket — so the first request 401'd and both dropdowns fell back to "None
  found" even with TTS engines and media players configured. They now refetch
  once the token lands.

## [1.5.0] — 2026-07-05

Completes Spanish support and bundles a broad correctness / security /
accessibility hardening pass (38 changes merged since 1.4.1).

### Added

- **Spanish support is now complete.** `es.json` reached full parity with
  `en.json` — the v1.4.0 `setup.tts` narration block (13 keys) was the last
  gap and is now translated (#373). Three new **native** (not translated)
  Spanish question packs ship alongside the existing `geografia-es`:
  **Naturaleza** (`nature`), **Ciencia** (`science`) and **Historia**
  (`history`), 150 questions each, registered in `versions.json` at 1.0. All
  450 new questions were fact-reviewed before release (5 corrections, 0
  removals). Spanish now has 4 packs / 600 questions. Pack discovery is
  data-driven, so no HTML changes were needed.

### Security

- Gate host-only `/api/quizify/*` endpoints on the admin session token so an
  unauthenticated device can no longer read player names / entity ids (#356).
- Stop wiping the admin token during admin-as-player games, closing a
  same-LAN host-takeover window (#351).
- Require the admin token for a crown transfer, and strip an inherited admin
  crown on a token-less lobby name-rejoin (#358, #389).
- Rate-limit and bound the unauthenticated flag POST (#357).
- Move the admin token out of the WebSocket URL and add a per-IP WS
  connection cap (#359, #361).

### Fixed

- Accessibility + UX sweep: larger TV reveal/standings text for couch
  legibility (#376), safe-area headroom + contrast fixes on the light theme
  (#375, #382), visible keyboard focus, screen-reader labels on the timer and
  dialogs (#380), `prefers-reduced-motion` honoured (#381), ≥44px touch
  targets (#379), TV lobby QR + live roster (#374), plus a batch of clipped
  labels, off-centre toggles and dead confirm/kick modals.
- Scoring / game-flow edge cases around Steal, estimation rounds and the
  Lightning Round so points land correctly, and steadier reconnection /
  roster handling (#405–#412, #448–#451, #472, #484).

### Performance

- Lighter hot paths — leaderboard, finale recompute, analytics memoisation,
  roster coalescing, shared HTTP session, cached unauthenticated pack-updates
  fetch (#360, #413–#418, #452–#457).

## [1.4.1] — 2026-06-20

### Fixed

- **"Open Quizify" now works inside the Android Home Assistant Companion App
  (#348).** The launcher button is a plain `<a target="_blank">` link, which
  the Android Companion's embedded WebView silently swallows — no tab opened
  and the button appeared dead. The launcher now detects the Companion Android
  user-agent and, in that case only, navigates the panel frame straight to
  `/quizify/admin` instead, bypassing the dead `target="_blank"`. iOS Companion,
  desktop and standalone browsers are unchanged and keep the native fullscreen
  new-tab behaviour. (Ported from the matching Beatify fix.) www-only change.

## [1.4.0] — 2026-06-14

### Added

- **Full TTS narration — the HA speaker becomes the quiz master (#281).** An
  optional "Quizmaster voice" mode narrates the whole game loop through the
  configured `media_player`, so the host doesn't have to read every question
  off the screen (a real accessibility win for hands-free / eyes-free play).
  Narrated events, each with its own per-event toggle: the question text, the
  answer options (lettered A/B/C to match the TV grid, skipped for estimate
  rounds), the reveal (correct answer + who got it + standings, spoken as one
  combined utterance), streak milestones, a player-join welcome, and a
  once-per-round "time running out" countdown. The admin setup panel adds a
  master switch with the per-event toggles nested + dimmed beneath it, plus
  dropdowns to pick the TTS engine and speaker per game (falling back to the
  integration-options defaults). All phrases are localized DE/EN with an
  English fallback. The narration config is pushed to the server during the
  lobby (`configure_tts`) so player-join announcements work before the game
  starts. Off by default — a configured TTS entity stays silent until the
  host enables it.

## [1.3.1-RC1] — 2026-06-13

First release candidate after 1.3.0. Versions the estimation question type that
landed just after the 1.3.0 cut, and — crucially — bumps the version so the
service-worker cache invalidates and the new frontend actually reaches users.

### Added

- **Estimation / closest-guess question type (#275).** A new numeric question
  type (`type: "estimate"`) where players slide to a guess instead of picking
  A/B/C; scored by closeness (closest gets full points, the rest scale down by
  rank, exact hits earn a bonus, ties share a rank, non-guessers score 0). The
  reveal plots every player's guess on a horizontal number line with the true
  value pinned and the winner highlighted — on both the player and TV screens.
  Ships two built-in "Unnützes Wissen" estimation packs (`schaetzfragen-de`,
  `estimation-en`, 15 questions each). Multiple-choice packs are unaffected
  (`type` defaults to `multiple_choice`).

### Fixed

- **Estimation UI not reaching already-installed clients.** The estimation
  question type (#275) was deployed on top of 1.3.0 without a version bump, so
  the service-worker `CACHE_VERSION` (derived from the manifest version) never
  changed and clients kept serving the cached pre-estimation `player.bundle.js`.
  The symptom: estimation rounds rendered the A/B/C multiple-choice grid (and
  an empty card in lightning rounds) instead of the slider, with the answer
  value leaking above it. Bumping the version invalidates the service-worker
  cache so the estimation slider/number-line UI loads. (Backend and freshly
  loaded clients were already correct.)
- **First question of a game rendered the multiple-choice grid for estimate
  rounds (and dropped the image banner).** The admin-as-player redirect (and any
  mid-round reconnect) lands on the `game_state` snapshot rather than a
  `question_started` event, and the snapshot render path only forwarded
  `text`/`answers`/`timer`/`round`/`category` to the question renderer — it
  dropped `question_type`, `estimate` and `image_url`. So round 1 fell back to
  the A/B/C grid (empty card in lightning) while every subsequent round rendered
  the slider correctly. The snapshot path now forwards those fields too.
- **Estimate questions excluded from lightning rounds.** The lightning view is
  fast tap-an-answer (3 options, 15s) and has no slider, so an estimate question
  drawn into a lightning round rendered as an empty card. The lightning question
  pool now skips estimate questions (with an attempt bound so an all-estimate
  pack selection can't spin); estimation questions still appear in normal rounds.

## [1.3.0] — 2026-06-12

The final 1.3.0 release — the culmination of release candidates RC1–RC16. See
the GitHub release notes for the consolidated highlights; the RC entries below
carry the detailed per-candidate history.

### Fixed

- **Blocking `manifest.json` read on the event loop (#343).** Home Assistant's
  loop watcher flagged Quizify for reading `manifest.json` synchronously on the
  event loop — once at setup (`server/context.py`) and on the launcher serve path
  (`server/views.py`). The reads now happen off the loop (an executor job at setup
  plus a background refresh that maintains an in-memory version); the asset
  cache-buster still updates after a www-only deploy without a restart.

## [1.3.0-RC16] — 2026-06-12

Sixteenth release candidate — adds the first Spanish content and a power-up
balance fix on top of RC15, from live testing feedback.

### Added

- **First Spanish built-in pack — "Geografía" (#342).** A faithful native-Spanish
  translation of the Geography pack (150 questions, `language: es`). With a
  Spanish-language pack present, the data-driven language picker now shows the
  🇪🇸 flag out of the box and the questions play in Spanish. Refs #335.

### Changed

- **Power-ups are now capped at one per player per game (#340).** Previously one
  randomly chosen player was granted a power-up every round with no memory, so a
  player could receive several in a game while others got none. Now the per-round
  draw only considers players who have not yet received a power-up this game, and
  grants nothing once everyone has had theirs.

## [1.3.0-RC15] — 2026-06-12

Fifteenth release candidate for 1.3.0 — a large batch closing the entire
28-issue / 7-lens code review (correctness, security, concurrency, performance,
tests) plus three new player-facing features. All work landed via reviewed PRs
with green CI (lint + drift + mypy + pytest gates) and per-change mobile verifies.

### Added

- **Redesigned end-of-game screen (#338).** The results screen leads with a
  "Sieger" winner banner, shows the highlights as a compact horizontal chip row
  (replacing the vertical timeline), and renders the final standings as a
  scoreboard with horizontal score bars (length ∝ score) plus inline ⚡/🔥
  badges on the fastest-finger / best-streak players. The old superlative cards,
  highlights timeline, and the separate your-result block are retired.
- **Lightning Round is now an automatic mid-game event (#285).** Replaces the
  host-triggered entry from #42: exactly once per game it fires on its own at a
  uniformly random round inside the eligible window (rounds 3 … N−1; the first
  two and the last round are blocked). Games of ≤ 3 rounds skip it entirely. A
  new **Lightning Round** setup toggle (default ON) turns it off. The manual
  start/end host actions and their buttons were retired, and the recap's former
  dead-end "again" button is now "Continue game".
- **Spanish (es) language integration (#335).** The host language picker is now
  data-driven — it shows a flag for every language the installed packs actually
  carry (🇩🇪 / 🇬🇧 / 🇪🇸 …) as flag-only chips — and the in-game UI ships a full
  Spanish translation (`www/i18n/es.json`). `language: es` packs, including
  community packs, now surface and play under the Spanish flag with no
  workaround. The category/pack chips are generated server-side from pack
  metadata, so adding a pack in a new language no longer needs an HTML edit.
- **Frozen-overlay Ice Card + countdown for the Freeze lockout (#322).** The
  player targeted by a Freeze now sees a full-card "Ice Card" overlay with an
  animated countdown ring while locked out (respects `prefers-reduced-motion`);
  only the target sees it, with no timer leak or stuck overlay across rounds.
- **Seasonal packs with auto-surfacing (#276).** A pack may carry an optional
  recurring `season` window (`{"start": "MM-DD", "end": "MM-DD", "label": "…"}`,
  both bounds inclusive, wrap-around across the new year supported). While the
  window is active *today* the Featured Spotlight pins the seasonal pack
  (deterministic soonest-ending-first when several overlap) and the admin pack
  picker badges it with the label (e.g. "🎄 Weihnachten"). Outside every window
  behaviour is unchanged; packs without a `season` field are fully
  back-compatible. The World Cup / Weltmeisterschaft packs ship a June–July
  window as the first seasonal packs.

### Fixed

- **Answer card stayed highlighted on the next question (mobile).** On iOS the
  `:hover` state sticks to the last-tapped element, and because the answer
  buttons are reused across questions (their text is swapped, not recreated),
  the previously tapped answer kept showing the "selected" highlight on the
  following question even though nothing was pressed. The hover style is now
  guarded behind `@media (hover: hover)` so it only applies on devices that can
  actually hover.
- **Reconnect / resume robustness (#314).** `get_state`/`resume` now project
  per-player snapshots and keep the pause clock sane, so reconnecting players
  see their own correct state instead of a shared or stale one.
- **Three P1 quick-wins (#315).** Steal direction inversion, service-worker
  scope, and admin reconnect.
- **Worker hardening (#292, #305).** The pack-submission worker fails closed on
  a missing secret, tightens CORS, and escapes markdown injection.
- **Power-up button + host-gone escape hatch (#288, #299).** The power-up button
  now shows every round, and players get a reset escape hatch when the host
  disappears.
- **Backend bug-fix batch (#293, #298, #302, #303, #307, #309, #310).** A
  consolidated round of server-side correctness fixes from the review.
- **Reveal highlight correctness (#308, #311).** The server emits a per-player
  correct button index so the reveal highlights the right answer for everyone,
  and the admin skip button is no longer hidden.
- **Freeze lockout + wager timeout (#300, #301).** A frozen player is locked out
  for the full duration (clock keeps running); an explicit wager that times out
  keeps its stake.
- **Lightning recap dead-end + TV dashboard PAUSED/LIGHTNING handling (#294,
  #296).** The TV dashboard now renders the PAUSED overlay and the
  LIGHTNING / LIGHTNING_RECAP views (previously it stayed on the prior view).
- **TV-cast answer text rendered as `[object Object]` (#283).** The cast/TV
  dashboard now normalizes `{text, correct}` answer payloads to their `.text`,
  so the TV view shows the real answer text. Player phones were unaffected.

### Performance

- **Eliminate per-request wasted work (#304).** Removed redundant per-request
  computation on hot paths.

### Internal

- **CI gates added (#306, #328).** Lint (ruff E501 + B/SIM/UP/C4), a
  generated-artifact drift guard, a `mypy` type-check gate, and
  `QUIZIFY_REQUIRE_NODE=1` enforcement now run on every PR.
- **Code-tidy + test-coverage batch (#312, #313).** Import/type-hint cleanup, a
  deduped service wrapper, and new coverage for `sensor.py`, `__init__.py`
  (options-flow reattach), seasonal edges, and the worker contract.

## [1.3.0-RC14] — 2026-06-11

Fourteenth release candidate for 1.3.0 — the three deferred refactors from the
code review (the follow-up issues #269/#270/#271). Non-functional; behaviour
unchanged.

### Changed

- **Shared collaborator surface (#269).** Promoted the de-facto-public private
  members (`ConnectionManager._safe_send` → `send`, plus `iter_admin_and_dashboard_ws`,
  `revoke_token`, a `conn`/`last_settings`/`categories`/`aggregate_for_questions`
  surface) and updated ~25 call sites, so the layering seams have a real
  contract instead of underscore reach-ins.
- **Dispatch table for websocket messages (#270).** Replaced the if/elif chain
  and 13 copy-pasted admin-auth guards in `_handle_message` with a
  `{type: (handler, admin_required)}` table and one centralized guard
  (`reset_game` keeps its special path). Verified the admin-guard set is
  identical, with a test that pins it.

### Internal

- **Home Assistant test environment (#271).** Added `homeassistant` +
  `pytest-homeassistant-custom-component` as CI test deps so `config_flow` and
  `binary_sensor` are now exercised in CI (previously untested). Guarded so the
  base run without HA still works.

## [1.3.0-RC13] — 2026-06-11

Thirteenth release candidate for 1.3.0 — the last two groups from the
2026-06-11 code review (#252). 442 tests passing; the review epic is now closed.

### Changed

- **Security hardening + documentation (#259).** Constant-time admin-token
  comparison (`hmac.compare_digest`), a clarified proxy-aware rate-limit, and a
  new Security-model section in `DESIGN.md` documenting the LAN-open endpoint
  surface and the rule that remote exposure must be fronted by Home Assistant
  auth. Player-facing endpoints stay open by design (players have no HA login).
- **Code quality (#260).** Pruned ~dead CSS (Beatify music-quiz leftovers, the
  replaced ranked-bar finale, confetti/neon-button rules) and six unused server
  methods; extracted a shared `SlidingWindowLimiter`; named the start-grace
  constants; translated the German server fallback strings to English. Added 31
  tests (rate limiter, token store, the non-top-score superlatives, featured-pack
  rotation). The larger private-attribute and dispatch-table refactors are
  tracked as follow-ups.

## [1.3.0-RC12] — 2026-06-11

Twelfth release candidate for 1.3.0 — the actionable fixes from the 2026-06-11
comprehensive code review (#252). 406 tests passing.

### Fixed

- **Reconnect/mid-round answers are scored correctly again (#253, CRITICAL).**
  The player-agnostic state snapshot served answers in canonical order while
  submissions are mapped through the player's own shuffle — so a player who
  reloaded mid-question had ~2/3 of taps recorded as a *different* answer.
  Player snapshots now project the answers through that player's shuffle (and
  the reveal snapshot matches the live shape, and the clock follows the player's
  timer).
- **Power-up correctness (#254).** Joker no longer risks greying out the correct
  answer (it now maps through the per-player shuffle); STEAL is restricted to
  targets who have answered (no more 0-point steal that burns the power-up); the
  freeze speed-bonus exploit is closed.
- **Round lifecycle (#255).** Late joiners are no longer scored 0 every round
  (`joined_late` is cleared after each round); a round where everyone
  disconnects now evaluates instead of hanging; `end_game()` is idempotent (no
  double finale broadcast / duplicate analytics).
- **Community-pack worker hardening (#256).** The integration can now
  authenticate to the worker with a shared secret (`X-Quizify-Secret` + a new
  option), the submission store is lock-guarded, and a cross-language contract
  test pins the pack schema so worker/integration can't drift.
- **Frontend (#257).** The pack-update banner renders again (an out-of-scope
  `_t` ReferenceError was swallowing it) and its pack metadata is now escaped
  (closing a latent stored XSS); the join button no longer hangs if the socket
  is dead at click; a power-up target-picker listener leak and a double-escaped
  podium name are fixed.
- **Performance (#258).** The ~2 MB question-pack load is preloaded off the event
  loop at setup, and per-player broadcasts are gathered (one stalled client no
  longer delays the room).

## [1.3.0-RC11] — 2026-06-11

Eleventh release candidate for 1.3.0 — a batch of fixes + the final icon and
results-screen polish.

### Fixed

- **Admin can no longer double-join as a player (#244).** The "Join as Player"
  control stayed tappable after the admin had already joined, so a fast second
  tap created a duplicate/ghost player. The control now no-ops + disables on
  join, and the server rejects a second self-join over the same connection
  (defense in depth, mirroring #207).

### Changed

- **SVG icons for the reveal-feedback + toast strings — P3 (#220).** The last
  emoji used as icons (✅❌ reveal chips, 🔥 streaks, 💔 streak-lost, 🥷 steal,
  🧊 freeze, 🎴 joker, 💡 hint, 🎉 thanks, ⚡🎯 bonuses) are pulled out of the
  translated strings and rendered as Rounded Duotone line glyphs. Completes the
  emoji→SVG icon migration (#212); language flags + the reaction bar stay emoji
  by design.
- **Consistent results-screen standings + action buttons (#245, #246, #247,
  #248).** The lightning recap and the end screen now share one medal-card
  standings row (gold/silver/bronze rank discs, highlighted current player,
  aligned scores, truncating long names) and one compact action-button row
  (full-width primary over single-line secondaries) instead of bare ranking
  text and oversized multi-line buttons. The lightning splash start bar is
  tightened to a single-line button.

## [1.3.0-RC10] — 2026-06-11

Tenth release candidate for 1.3.0.

### Fixed

- **A new release number now resets the client cache (#243).** Home Assistant
  serves `/quizify/static/*` with a 31-day `max-age`, and the service worker
  precached un-versioned URLs and fetched plainly in its network-first path —
  so both answered from the browser's month-long HTTP cache instead of the
  server, and stale JS/CSS survived release bumps ("network-first" was really
  "HTTP-cache-first"). Precache URLs now carry the `?v=<version>` buster and use
  `cache: 'reload'`; un-versioned same-origin requests are fetched `no-cache`;
  the service worker registers with `updateViaCache: 'none'` and updates on
  every load; all remaining HTML asset refs are versioned. A version bump now
  re-fetches `sw.js`, rolls `CACHE_VERSION`, drops the old caches, and pulls
  every asset fresh. (One manual site-data clear is needed once to retire a
  service worker registered before this fix.)

## [1.3.0-RC9] — 2026-06-11

Ninth release candidate for 1.3.0.

### Changed

- **SVG icons for setup presets, end-game awards, and the highlights tab — P2 (#219).**
  The setup mode presets (Quick/Classic/Marathon/Custom), the seven end-game
  award discs, and the highlights tab now use the shared Rounded Duotone set
  instead of emoji. Award glyphs resolve client-side from the stable award key,
  leaving the server unchanged. Completes the emoji→SVG icon migration except
  for the reveal/toast strings (P3 #220).

### Fixed

- **Lightning round renders on admin-as-player reconnect (#239).** Reconnecting
  into a live lightning round left the screen blank: the three lightning view
  containers were never registered in `showView`'s list, so the function hid
  every other view but could not un-hide the lightning one. Registering them
  lets the lightning round actually render on reconnect (the data was already
  carried by the #221 snapshot fix). The RC8 blank-screen watchdog remains as a
  belt-and-braces fallback.

## [1.3.0-RC8] — 2026-06-10

Eighth release candidate for 1.3.0.

### Fixed

- **Player never shows a blank screen (#237).** A dead-reconnect URL
  (`?name=X&admin=true&reconnect=1` with no live session) sent a join that
  yielded no `game_state`, so neither the failed-reconnect handler (#227) nor
  the game-state fallback (#228) fired and the player was left on no view. A
  boot watchdog now falls back to the join screen ~4s after load if no real
  view has rendered, so the player always has a way forward.

## [1.3.0-RC7] — 2026-06-10

Seventh release candidate for 1.3.0. Two gameplay/mobile fixes found in live
testing and reproduced in a real game.

### Fixed

- **In-game leaderboard no longer empty during a question (#235).** The panel
  showed "--" mid-round because the leaderboard arrives via the `game_state`
  broadcast (at round start / reveal), not in `question_started`, and the
  in-game panel was never fed it — only the reveal's own list was. It now
  updates from any `game_state` that carries a leaderboard, so the standings
  show during a live question.
- **Admin control bar (Pause/End) pins to the bottom on iOS Safari (#232).** A
  `position: fixed` element with `backdrop-filter` is a WebKit bug — Safari
  positioned the bar relative to its container instead of the viewport, so it
  floated mid-page on iPhone (Chrome was unaffected). The bar was already 96%
  opaque, so the near-invisible blur was dropped for a solid background.

## [1.3.0-RC6] — 2026-06-10

Sixth release candidate for 1.3.0. Mobile polish found in live testing.

### Fixed

- **Start (and other primary-button) icons are legible again.** The P4 SVG
  icons sat in a tinted disc that washed out on the coral `.btn-primary` fill
  (notably the "Start Game" play glyph). Icons inside a filled primary button
  now drop the disc and render white, matching the button's white label;
  secondary / outline buttons keep their tinted discs.
- **Top content no longer clips under the iOS status bar (#229 / #233).** On a
  scrolled player/end screen (e.g. the podium or a question), content slid under
  the status bar. The player header is now sticky + opaque, so scrolled content
  is masked by the header instead of the bare status bar — works in a Safari tab
  regardless of safe-area insets; the standalone PWA also gets the Apple status
  metas + notch inset.

## [1.3.0-RC5] — 2026-06-10

Fifth release candidate for 1.3.0. Finishes the emoji→SVG icon sweep and fixes a
blank-screen edge found while live-testing P4.

### Changed

- **Remaining emoji UI icons replaced with SVG line icons — P4 (#225).** The
  standalone emoji-as-icon surfaces missed by the original #212 P1 inventory now
  use the shared Rounded Duotone set (`window.QuizifyIcons`): the admin lobby
  (Cast to TV, Join as Player, Start), the player nav/section icons (controller,
  target, brain, trophy, party, hourglass, sparkle, bulb) and error/hero states,
  the game control bar (skip, pause, resume, end, finish) and the paused screen,
  plus the status/utility glyphs (connection-lost antenna, invite-copy clipboard)
  and the lightning bolt icons. A new `UI_ICON_SVG` map + `uiIcon(name)` accessor
  back these; a `paintUiIcons()` pass swaps the `data-ui-icon` spans on init.
  Language flags and the floating reaction bar emoji are intentionally retained;
  emoji embedded in translated strings (P3 #220) and the setup presets/awards
  (P2 #219) are unchanged.

### Fixed

- **Player no longer shows a blank screen after a failed reconnect (#227).** The
  `reconnect_failed` handler cleared the session but never routed to a view, so a
  dead/stale reconnect (no joinable game) left every view hidden. It now returns
  to the join screen, and a fallback was added so an unmapped game-state phase
  always shows a usable view instead of nothing (same class as #221).

## [1.3.0-RC4] — 2026-06-10

Fourth release candidate for 1.3.0. Completes the event-loop-blocking cleanup
started in RC3.

### Fixed

- **Asset fingerprint no longer blocks the event loop (#213).** The cache-buster
  `scandir` over the `www/` tree now runs in an executor thread instead of on the
  loop (with the existing 5 s cache retained), so it can't stall the WebSocket
  server. Pairs with the RC3 history write/read fix (#222) to close out the whole
  blocking-I/O-on-the-loop class.

## [1.3.0-RC3] — 2026-06-10

Third release candidate for 1.3.0. Fixes two issues found while live-testing RC2.

### Fixed

- **Lightning Round no longer renders a blank player screen (#221).** A
  `game_state` snapshot sent during the lightning phase (e.g. the live
  leaderboard-refresh broadcast) omitted the `lightning` sub-state, so the
  client landed on an empty lightning view. The snapshot builder now carries the
  same `lightning` payload the reconnect path already had (splash-pending state
  or the current question).
- **`end_game` no longer blocks the event loop writing `question_history.json`
  (#222).** The history write — and the analogous history read at setup — now run
  in an executor thread instead of on the loop, so finishing a game no longer
  stalls the WebSocket server for all clients (same class as #213).

## [1.3.0-RC2] — 2026-06-10

Second release candidate for 1.3.0 (the first `v1.3.0` pre-release is RC1).
Adds a shared, app-wide SVG line-icon system that replaces the last emoji used
as UI icons, plus the welcome-screen redesign and two live-test fixes that
landed after the RC1 tag.

### Added

- **App-wide SVG line-icon system — "Rounded Duotone" (#212).** A shared icon
  helper (`www/js/icons.js`, `window.QuizifyIcons`) is now the single source of
  truth for the category/theme glyphs, consumed by both the admin and player
  JS. Style chosen from a design shotgun: a 2 px round-stroked glyph over a
  soft accent-tinted backing disc (flat tints, no gradients). First applied to
  the detail-view pack cards and the theme filter tabs; the emoji are pulled out
  of the `theme.*` labels so the strings hold text only. (Presets, end-game
  awards, and reveal-feedback strings are tracked as follow-ups #219 / #220.)

### Changed

- **Welcome screen redesign ("Categories-forward").** The host setup screen now
  leads with the category picker as a two-column grid of color-tinted tiles, each
  with an **SVG line icon** (replacing the emoji), the category name, and its
  question count. Tiles tint by theme across the four Soft Parlor accents and show
  a coral border + check when selected. The featured pack (World Cup / WM) gets a
  refreshed "Soft Spotlight" card: an SVG trophy in a sun-tinted badge, an
  "Empfohlen · Neu" eyebrow, and a round coral selection control. Selection wiring,
  language filtering, and the start payload are unchanged — the grid is still built
  from `#category-chips` as the single source of truth.

### Fixed

- **Host reset is authorized again; orphaned admin crown recovered (#207).** The
  single-admin invariant compared only by name, so the admin-as-player redirect
  could re-join as a second host and orphan the crown, making the legitimate
  host's Reset (and Pause/Skip) a silent no-op. Only a connected admin now blocks
  a re-claim, and Reset has an explicit recovery path.
- **Safe service-worker auto-reload on idle screens (#215).** The PWA now only
  auto-reloads on idle screens, avoiding a refresh mid-interaction.

## [1.3.0-RC1] — 2026-06-09

Feature release on top of the 1.2.7 hardening: a new Lightning Round bonus
mode, in-app community pack submission, optional question images, group
adaptive difficulty, lobby music, sound effects, a dramatic finale countdown,
plus a large internal refactor of the game server and two live-test bug fixes.

### Added

- **Lightning Round bonus mode (#42).** A fast bonus round the host can trigger
  from the finale (or standalone from the lobby): an intro splash explains the
  rules, then **5 quick questions at 15 s each** with no reveals between, and an
  end recap that shows the **correct answer** for every question (plus your own
  wrong pick where you missed). Admin-only trigger; players see a "waiting for
  host" hold on the splash until the host starts.
- **Sound effects with a mute toggle (#177).** Correct / wrong / last-5-seconds
  audio cues on the player device, with a mute control in the player header that
  persists across reloads.
- **Submit a community pack in-app (#180).** Hosts can now compose or paste a
  community question pack as JSON directly in the admin setup screen, get it
  validated field-by-field (a per-row ✓/✗ check of the pack name, language,
  question list and every question's shape — mirroring the on-disk pack schema
  from #179), and submit it for review. A submitted pack is handed to a small
  worker that turns it into a GitHub issue in `mholzi/quizify`; the GitHub
  token lives only in that worker, never in the browser or the integration.
  Each submission's status is reconciled server-side against the GitHub issue
  state (throttled to ~hourly): a closed-as-completed issue shows as
  *Accepted*, a closed-as-not-planned issue as *Declined*. Error messages are
  localized (`INVALID_FORMAT` / `RATE_LIMITED` / `GITHUB_ERROR`) with a
  fallback to the raw worker message. The whole feature is **inert by
  default**: a new optional "Community pack submission URL" option must be set
  before the section appears — empty means the UI stays hidden and the
  endpoints accept nothing. (The worker route itself is separate
  infrastructure and is not part of this integration.)
- **Group adaptive difficulty (#40).** A new **Auto** difficulty option that
  tunes the whole table together within a single game. The game still serves
  one shared question to everyone per round; after each round Quizify looks at
  the group's overall correct-rate and nudges the difficulty of *upcoming*
  questions up (group acing it) or down (group struggling). It is deliberately
  conservative: it starts at medium, only steps one rung at a time, averages
  over the last few rounds to avoid swinging on a single lucky/brutal question,
  and stays put until enough rounds of signal exist. Fixed Easy/Medium/Hard
  picks are untouched — calibration only runs in Auto mode. Per-player adaptive
  difficulty (personalised question streams) is tracked separately in #186.
- **Waiting-room music (#56).** Optional ambient background audio in the
  lobby, played through a real Home Assistant speaker. A new "Lobby music URL"
  option lets you point Quizify at an audio file you supply yourself
  (e.g. `/local/quizify-lobby.mp3` from your `config/www` folder); Quizify
  loops it on the **same `media_player` entity used for TTS** while waiting for
  players (via `media_player.play_media` + best-effort `repeat_set`). Music
  stops automatically once the game starts, so it never overlaps in-game TTS
  announcements that share the same speaker. The mechanism is inert by default
  — no audio file ships with the integration, and nothing plays until both a
  media player and a URL are configured.
- **Optional question images (#25).** Questions may now carry an optional
  `image_url` field in their pack JSON. When present, the shared dashboard
  renders the image above the question text and player screens show a
  thumbnail — handy for "What film is this from?" or visual geography
  questions. Only absolute `http(s)` URLs are accepted; relative paths,
  `data:`/`javascript:` schemes and non-strings are dropped at parse time.
  Questions without an image render exactly as before.
- **Dramatic finale countdown (#182).** Before the final question of a game, the
  shared dashboard plays a short suspenseful countdown so the last round lands
  with more weight. On any error the question still appears — the countdown
  never blocks the game.

### Changed

- **Finale podium redesign on player phones ("Podium Reborn").** The end-of-game
  ranking on each player's phone now shows the top three as bolder rising blocks
  with a warm tonal fill (1st coral, 2nd sage, 3rd sky), white numerals, and the
  medal accent kept on the top edge (sun-yellow / silver / bronze). A soft warm
  halo rises from the champion's block for a more celebratory finish. Scoped to
  the player end screen only — the admin / TV host podium keeps its cream-shelf
  look. Picked from a four-direction design exploration; the rest of the finale
  (your-result stats, awards, highlights timeline, full rankings) is unchanged.
- **Image questions side-by-side (#195).** Image questions present the picture
  beside the question text with tap-to-zoom, instead of a small thumbnail.
- **Finale countdown styling (#196)** refined to a "Spotlight-Marquee" look, and
  the **sound mute control (#197)** folded into a tidy header cluster.

### Fixed

- **Reset now fully clears the game (#207).** Pressing reset removes all players
  *and* the host and returns every screen to the initial setup — previously the
  reset signal was sent after connections were already closed, so screens froze
  in the old state.
- **Only one host per game (#208).** A second admin claim is now rejected, so
  two crowned hosts can no longer coexist in one lobby.
- **i18n name validation (#171)** deduplicated into one shared rule across the
  player and admin join paths.
- **Surfaced swallowed errors (#170)** in broad `except` loops so failures are
  logged instead of silently dropped.

### Internal

- Large game-server refactor splitting the state/websocket "god objects" into
  focused units — ScoringEngine + BroadcastDispatcher (#187), RoundMessageBuilder
  (#189), PhaseController (#188) and the timer-tick relocation (#203) — all
  behaviour-preserving with regression tests (part of #184).
- CSS split into per-screen source modules with a concat build (#185), test
  event-loop isolation to fix cross-module pollution (#198), and perf/leak
  invariant tests (#169).

## [1.2.7] — 2026-06-09

Backend hardening release from the 2026-06-09 automated code review. No
user-facing UI changes — concurrency and security fixes in the game server,
each with regression tests (162 passing).

### Fixed

- **Reaction-bonus cross-game leak (#167).** The inbound reaction-bonus
  counter was never cleared between games, so a player capped on reactions in
  round N of one game could be wrongly blocked from earning the bonus in
  round N of the next game. It now resets with every new game.
- **STEAL on a vanished target (#167).** If the steal target left the game
  before the power-up applied, the client still played a "successful" steal
  animation for zero points. STEAL now returns an error instead of
  broadcasting a hollow effect.
- **Admin-bootstrap race (#168).** On a fresh install, two simultaneous first
  connections could both be granted admin while only one token persisted —
  silently locking the other admin out. Bootstrap now grants exactly one admin
  atomically under a lock.
- **Session-token memory growth (#168).** Issued-but-never-validated session
  tokens were only evicted on lookup, so they could accumulate unbounded (a
  DoS surface). Expired tokens are now swept opportunistically on issue.
- **Malformed pack file 500 (#168).** A pack file that was valid JSON but not
  an object (e.g. a list) crashed the admin setup screen. It now degrades
  gracefully to the default icon.

### Verified (no code change needed)

- Double round-evaluation, double-submit, pending-removal-on-reset (#167) and
  the shuffle/answer-index bound (#168) were confirmed already safe under the
  cooperative single-threaded asyncio model and locked in with regression
  tests against future refactors.

## [1.2.6] — 2026-06-08

### Changed

- **Removed the "New version available" reload banner.** Now that the asset
  cache-buster is a content fingerprint, the next page load already pulls the
  fresh version, so the banner was redundant. Dropping it (and its auto-reload)
  also means an always-on host screen never reloads itself mid-game. The service
  worker still handles PWA install and offline caching, and refreshes silently.

## [1.2.5] — 2026-06-08

Consolidated release of the 1.2.x line under a fresh version number for clean
distribution. Bundles everything since 1.1.0: the World Cup packs, the
first-screen pack picker (with World Cup as a selectable pack), host-screen
language handling, and the self-healing asset cache-buster. See the 1.2.0–1.2.3
entries below for the detailed history.

## [1.2.3] — 2026-06-08

### Fixed

- **Host screens follow your Home Assistant language.** The launcher, dashboard,
  and analytics pages flashed English and then flipped to the browser language,
  ignoring the HA setting. They now resolve the HA language first (like the admin
  screen), so an English Home Assistant stays English. Player phones still follow
  the guest's own browser language.
- **World Cup is now a selectable pack, not an instant start.** Tapping the World
  Cup card on the setup screen used to launch the game immediately. It now toggles
  the pack on/off with a checkmark, exactly like the other categories — pick it (or
  any pack) and start with the “Start Game” button.

## [1.2.2] — 2026-06-08

### Fixed

- **Asset cache-buster no longer depends on bumping the version.** Static
  assets are served immutable, so the `?v=` query string is the only way to
  force a refetch — and it was keyed only to the manifest version. A reused or
  forgotten version bump left `?v=` unchanged, so browsers kept serving old
  CSS/JS/i18n (the World Cup card showed English on a German setup). The `?v=`
  params and the service-worker cache key now derive from a fingerprint of the
  asset files, so any CSS/JS/i18n change invalidates caches automatically. The
  displayed version stays clean. Resolves the recurrence of #147.

## [1.2.1] — 2026-06-08

### Fixed

- **Stale assets after updating.** While in beta, 1.2.0 was built twice under
  the same version number, so the `?v=` cache-buster never changed and browsers
  kept serving the old CSS/JS — the World Cup card showed English text on a
  German setup and the new pack chips didn't render. 1.2.1 bumps the version so
  clients fetch the current assets. No functional change beyond 1.2.0.

## [1.2.0] — 2026-06-08

### Added

- **World Cup quiz packs (English + German).** A new Men's FIFA World Cup
  category in the surprising-trivia style — `World Cup` (100 questions) and
  `Weltmeisterschaft` (99). Generated and reviewed for factual accuracy;
  selectable from the category list like any other pack.
- **Pick your pack from the first screen.** The setup screen now carries the
  pack picker itself — a featured World Cup spotlight card plus every pack as a
  chip — so the host chooses what to play in one tap, without opening “Adjust
  settings”. Game settings (difficulty, rounds, timer) stay there. The featured
  card and chips follow the active language (World Cup ↔ Weltmeisterschaft).

### Fixed

- **Category cards show the right question counts again.** After the +50-per-pack
  update the setup-screen cards still read ~100; they now reflect the real
  counts (150, or 148–149 where review removed an ambiguous question).

## [1.1.0] — 2026-06-08

### Added

- **+50 questions per category — the library grows from 1,800 to ~2,690.**
  Every one of the 18 packs (9 themes × German/English) gained 50 fresh
  "Unnützes Wissen" questions: surprising, counter-intuitive, weird-but-true,
  never capital-of-X or year-of-Y lookups. Each new batch was deduplicated
  against the existing questions in its pack, then run through a factual /
  distractor / fun-fact review — 6 ambiguous or disputed questions were
  dropped rather than shipped, so a few packs land at 148–149.
- Every pack version bumped `1.0 → 1.1`, and `versions.json` now tracks all
  18 packs (was 6) so existing installs are offered the new questions via the
  in-app pack update-check.

## [1.0.1] — 2026-06-08

### Fixed

- **Admin UI now follows your Home Assistant language.** The setup screen
  defaulted to German on first visit, so English speakers saw a flash of
  English that switched to German and stayed there with no obvious way back
  ([#152](https://github.com/mholzi/quizify/issues/152)). The admin interface
  now uses Home Assistant's configured language (Settings → General). Any
  non-German language (French, Spanish, …) falls back to English, since the UI
  ships in German and English only. The 🇩🇪/🇬🇧 toggle still switches the UI for
  the current session.

## [1.0.0] — 2026-06-07

The first official release of Quizify — a multiplayer trivia party game that
lives entirely inside Home Assistant. The TV is the host, phones are the
buzzers, and there's nothing to install for your guests.

### 🎉 Everything in 1.0.0

- **Scan and play.** The TV shows a QR code; everyone joins on their phone.
  No apps, no accounts, no logins. It all runs on your local network.
- **1,800 questions across 18 packs.** Nine themes — Geography, Animals &
  Nature, Pop Culture, Sport, Music, Science, History, Food & Drink, and
  Technology — each a clean 100 questions, in both German and English.
- **Pick your game.** Quick round, Classic, or Marathon presets, or go custom:
  choose your own topics, difficulty, round count, and timer.
- **Power-ups.** Joker, Double Points, Freeze, Steal, and a time boost turn a
  quiz into a party.
- **Streaks and bonuses.** Speed bonuses, difficulty multipliers, and streak
  fireworks at 3, 5, and 7 in a row.
- **A finale worth waiting for.** A podium, per-player superlatives, a
  highlights reel, and a full ranked leaderboard.
- **Soft Parlor design.** Warm cream paper, a four-color palette, and
  typography built to read across the room — cozy and friendly, like a family
  board game.

---

**18 packs · 1,800 questions · 2 languages · runs entirely on your local network**

[Report a Bug](https://github.com/mholzi/quizify/issues) · [Discussions](https://github.com/mholzi/quizify/discussions)
