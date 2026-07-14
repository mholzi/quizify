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

## Where the bundled files come from

The four `.mp3` files here are **generated, not sourced**. `generate_sfx.py`
synthesizes them from scratch with the Python stdlib (additive synthesis, bell-ish
stabs in a game-show register), and the script is the authoritative source — edit
it and re-run it rather than hand-editing the audio:

```bash
python3 generate_sfx.py                       # writes correct/wrong/streak/winner.wav
for f in correct wrong streak winner; do      # then encode to mp3
  ffmpeg -y -i $f.wav -codec:a libmp3lame -b:a 128k -ar 44100 $f.mp3
done
```

Because they are original synthesized waveforms, there is no third-party licence
attached to them at all — no CC0 sourcing or attribution needed.

## Rules

- Any **third-party** audio dropped in here must be **CC0 / public domain** (or
  otherwise cleared for redistribution). Do not commit copyrighted audio.
- The feature stays **silent** for any cue whose file is absent (and which has no
  per-cue override URL set in the options flow), so removing a file is a valid way
  to mute a single cue.
- Keep them **short — under ~2 seconds** (the bundled set runs 0.7–1.6 s). They
  one-shot on the same speaker used for TTS announcements and lobby music; a cue is
  dropped entirely while a TTS announcement is live so it never talks over it.
- A per-cue override URL (options flow: *Correct/Wrong/Streak/Winner sound URL*)
  takes precedence over the bundled default here.

## Cache / fingerprint note

This directory is intentionally **outside** the asset-fingerprint set
(`css`, `js`, `i18n` — see `server/views.py` `_ASSET_SUBDIRS`), so adding audio
here does not move the frontend cache-buster or trip the generated-artifact
drift test.
