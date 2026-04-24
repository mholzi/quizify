# Quizify

Multiplayer trivia quiz game for Home Assistant. Players scan a QR code, answer on their phones, watch the TV host screen. No apps, no accounts, runs entirely on the local network.

## Design System

**Always read [DESIGN.md](./DESIGN.md) before making any visual or UI decisions.**

All typography, color, spacing, motion, layout, and aesthetic direction is defined there. The direction is *"Broadcast Living Room"* — the posture of a televised game show delivered at home scale. Memorable-thing anchor: *"It felt like a real game show on my TV."*

Key constants (do not deviate without explicit user approval):
- Primary accent: broadcast gold `#F4C430` (TV-bright trophy color — nothing else in the category uses this saturation)
- Background: studio navy `#0B1739` (deep saturated royal blue — category table stakes for game shows)
- Display type: Unbounded (rounded-geometric heavy sans) — never Fraunces, never Inter, never Space Grotesk
- Body/UI type: Instrument Sans — never Inter, Roboto, Open Sans, or system-ui
- Mono type: JetBrains Mono — for all scores, timers, metadata
- Primary text: warm parchment `#F4EBCF`, not pure white
- Dark is the primary mode (light mode available for admin daytime use)
- Never use: purple gradients, cartoon mascots, confetti on finale, bubbly 16px+ radii, neon saturated colors, pure white text

In QA / design-review mode, flag any code that doesn't match DESIGN.md. In pre-landing review, call out typography or color deviations explicitly.

**Note:** A previous direction (Editorial Game Show — Fraunces + amber-brass `#E8B047` + cream `#F5EEDC`) was replaced on 2026-04-24. If you see those tokens in existing code, they represent drift to fix, not intent to preserve.

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
