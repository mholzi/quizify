# Quizify

> Multiplayer Useless Knowledge Quiz Game for Home Assistant

[![HACS Default](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)

## What is Quizify?

A HACS integration that turns your Home Assistant into a multiplayer trivia quiz game.
Players join via QR code on their phones — no app needed.

## Features
- 3 answer options per question with fun facts after each round
- Timer-based scoring with speed bonuses
- Difficulty levels (Easy / Medium / Hard) with different time limits
- 4 power-ups: Joker, Double Points, Freeze, Time Boost
- 150+ questions across 3 categories (more coming)
- Shared screen / dashboard mode for TV
- Runs fully local — no cloud, no accounts, MIT license

## Installation

1. Install via HACS (search for "Quizify")
2. Restart Home Assistant
3. Go to Settings > Integrations > Add Integration > Quizify
4. Open /quizify/ in your browser

## Usage

1. Open /quizify/admin on your device
2. Select category, difficulty, and round count
3. Players scan QR code or visit the join URL
4. Start the game!

## Question Categories

| Category | Language | Questions |
|---|---|---|
| Geographie | DE | 50 |
| Tiere & Natur | DE | 50 |
| Popkultur | DE | 50 |

## Development

See IMPLEMENTATION_PLAN.md for architecture details.

## License

MIT
