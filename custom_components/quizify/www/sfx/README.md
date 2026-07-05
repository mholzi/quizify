# Room sound effects (SFX)

"The House Plays Along" Phase 3 (#494) plays a short one-shot sound on the
configured `media_player` at four game milestones. This directory holds the
**bundled default** sounds, served at `/quizify/static/sfx/<cue>.mp3`.

## Expected files

| Cue      | Filename      | Plays when…                                            |
| -------- | ------------- | ------------------------------------------------------ |
| correct  | `correct.mp3` | the crowd mostly got the answer right (strict majority) |
| wrong    | `wrong.mp3`   | the crowd mostly missed the answer                     |
| streak   | `streak.mp3`  | a player hits an answer streak                         |
| winner   | `winner.mp3`  | the winner is decided                                  |

## Rules

- **No audio ships in the repo.** These files are **user-supplied**. The feature
  stays silent for any cue whose file is absent (and which has no per-cue
  override URL set in the options flow).
- Files **must be CC0 / public domain** (or otherwise cleared for
  redistribution). Do not commit copyrighted audio.
- Keep them **short — under ~2 seconds**. They one-shot on the same speaker used
  for TTS announcements and lobby music; a cue is dropped entirely while a TTS
  announcement is live so it never talks over it.
- A per-cue override URL (options flow: *Correct/Wrong/Streak/Winner sound URL*)
  takes precedence over the bundled default here.

## Cache / fingerprint note

This directory is intentionally **outside** the asset-fingerprint set
(`css`, `js`, `i18n` — see `server/views.py` `_ASSET_SUBDIRS`), so adding audio
here does not move the frontend cache-buster or trip the generated-artifact
drift test.
