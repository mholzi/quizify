# Community Question Packs

> **⚠️ Do not drop your own packs in this folder.** It lives inside
> `custom_components/quizify/`, which HACS **replaces wholesale on every
> update** — anything you put here is deleted the next time Quizify updates.
> This folder now only carries `example-pack.json`, shipped with the
> integration as a reference.

## Where your packs go

Put your own packs in your **Home Assistant config directory**:

```
<config>/quizify/packs/my-pack.json
```

(the same `<config>` that holds `configuration.yaml`; on Home Assistant OS that
is `/config`, so `/config/quizify/packs/`). Quizify creates the folder on
startup. It is outside `custom_components/`, so updates never touch it.

**If you already had packs in this folder**, you do not have to do anything:
on the next start Quizify **moves** every `*.json` it finds here (except the
shipped `example-pack.json`) into `<config>/quizify/packs/` and logs the move.
If a file of the same name is already there, your copy in `<config>` wins and
the old file is left alone rather than overwritten.

## Loading a pack without restarting

After adding, editing or removing a pack, call the

```
quizify.reload_packs
```

service (Developer Tools → Actions → "Quizify: Reload question packs"), then
reload the admin page. The pack shows up in the category picker alongside the
built-in ones. The service only works from the lobby — a running game keeps the
packs it started with. A Home Assistant restart works too, of course.

Community packs are kept separate from the built-in, hand-reviewed packs so
that user content is clearly namespaced and can be validated independently.

## File format

A community pack is a single JSON object:

```json
{
  "name": "My Custom Pack",
  "language": "en",
  "version": "1.0",
  "theme": "general",
  "questions": [
    {
      "id": "mypack_001",
      "question": "What is the capital of France?",
      "answers": [
        {"text": "Paris", "correct": true},
        {"text": "Lyon", "correct": false},
        {"text": "Marseille", "correct": false}
      ],
      "difficulty": "easy",
      "fun_fact": "Paris has been the capital of France since 508 AD.",
      "category": "My Custom Pack"
    }
  ]
}
```

### Top-level fields

| Field       | Required | Notes                                                        |
|-------------|----------|--------------------------------------------------------------|
| `name`      | yes      | Display name shown in the picker. Must be a non-empty string.|
| `questions` | yes      | Non-empty list of question objects (see below).              |
| `language`  | no       | `"de"`, `"en"`, or `"es"`. Defaults to `"de"`. A `"es"` pack is selectable directly — the admin language picker and category grid are built from the languages actually present in the loaded packs (#335), so no extra setup or `language: en` workaround is needed.|
| `version`   | no       | Free-form version string. Defaults to `"1.0"`.              |
| `theme`     | no       | Optional theme key used for the picker icon.                 |

### Question fields

| Field        | Required | Notes                                                  |
|--------------|----------|--------------------------------------------------------|
| `id`         | yes      | Unique within the pack. Duplicates are dropped.        |
| `question`   | yes      | The question text.                                     |
| `answers`    | yes      | Exactly **3** answers, exactly **one** marked correct. |
| `difficulty` | no       | `"easy"`, `"medium"`, or `"hard"`. Defaults `"medium"`.|
| `fun_fact`   | no       | Optional trivia shown on the reveal screen.            |
| `category`   | no       | Defaults to the pack `name`.                           |

## Validation & limits

Community JSON is treated as untrusted input. Loading is defensive: a malformed
pack is skipped (and logged) rather than crashing the integration. Concretely,
a pack is **rejected or trimmed** when:

- the file is larger than **1 MiB**;
- the JSON is invalid or not a top-level object;
- `name` is missing/empty, or `questions` is missing/empty;
- a question does not have exactly 3 answers with exactly 1 correct answer
  (such questions are skipped individually);
- an answer is not an object with non-empty `text` — `"answers": ["A", "B", "C"]`
  and `{"correct": true}` without `text` are both skipped, not fatal;
- more than **500 questions** are present (the list is truncated);
- the slug collides with an already-loaded pack.

Each pack is registered under a `community-<filename>` slug so it can never
shadow a built-in pack.

## Contributing upstream

To share a pack with the wider community, open a pull request adding your
`*.json` file to this folder. Keep it focused (one theme per pack) and run the
question bank tests before submitting.

**Maintainer note:** a pack merged into this folder ships with the release, so
add its filename to `SHIPPED_COMMUNITY_PACKS` in
`custom_components/quizify/game/questions.py`. Otherwise the first-run
migration treats it as host data and moves it into every user's
`<config>/quizify/packs/`, where the next update would re-create it here and
the two copies would fight over the same slug.
