# Quizify

Multiplayer trivia quiz game for Home Assistant. Players scan a QR code, answer on their phones, watch the TV host screen. No apps, no accounts, runs entirely on the local network.

## Design System

**Always read [DESIGN.md](./DESIGN.md) before making any visual or UI decisions.**

All typography, color, spacing, motion, layout, and aesthetic direction is defined there. The direction is *"Soft Parlor"* — warm paper, coral + sage + sky + sun accents, family-board-game posture. Memorable-thing anchor: *"Cozy and friendly — like a family board game."*

Key constants (do not deviate without explicit user approval):
- Primary accent: warm coral `#E88A7F` (hospitable, non-gendered, differentiated from category)
- Secondary accents: sage `#7FA897`, sky `#7FA8C4`, sun `#E8C47F` — used as a cohesive palette at equal muted saturation
- Background: cream paper `#FAF6EC` (NOT pure white, NOT dark — Soft Parlor is light-primary)
- Display type: DM Sans at 700-900 — never Unbounded, never Fraunces, never Inter.
  (Was Cabinet Grotesk; dropped in #738 because the ITF Free Font License forbids
  redistributing the files and fetching them from Fontshare leaked every guest's IP.)
- Body/UI type: DM Sans — never Inter, Roboto, Open Sans, Poppins, system-ui
- Mono type: JetBrains Mono — for all scores, timers, metadata
- All faces are bundled in `www/fonts/` and served locally. Never add a font
  `<link>` or `@import` pointing at a CDN — it breaks the no-data-leaves-your-network
  promise and blocks the first paint.
- Primary text: warm ink `#2A2820`, NOT pure black
- Light is the primary mode (dark mode available for late-night play)
- Never use: dark backgrounds on primary surfaces, neon colors, confetti, bubbly 20px+ radii, cartoon mascots, gradient CTAs, CRT/scanline/phosphor decoration

In QA / design-review mode, flag any code that doesn't match DESIGN.md. In pre-landing review, call out typography or color deviations explicitly.

**Note:** Previous directions (Editorial Game Show: Fraunces + amber-brass; Broadcast Living Room: Unbounded + studio navy + broadcast gold) were both superseded on 2026-04-24 by Soft Parlor. If you see any of those tokens in existing code, they represent drift to fix, not intent to preserve.

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill tool as your FIRST action. Do NOT answer directly, do NOT use other tools first. The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
- Generate Pretext-native HTML from approved design → invoke design-html
