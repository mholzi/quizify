# Community Question Packs

This folder holds **user-contributed** question packs. Any `*.json` file you
drop in here is discovered automatically the next time Quizify loads its
question bank (a Home Assistant restart, or an in-app pack reload). They appear
in the category picker alongside the built-in packs.

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
| `language`  | no       | `"de"` or `"en"`. Defaults to `"de"`.                        |
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
- more than **500 questions** are present (the list is truncated);
- the slug collides with an already-loaded pack.

Each pack is registered under a `community-<filename>` slug so it can never
shadow a built-in pack.

## Contributing upstream

To share a pack with the wider community, open a pull request adding your
`*.json` file to this folder. Keep it focused (one theme per pack) and run the
question bank tests before submitting.
