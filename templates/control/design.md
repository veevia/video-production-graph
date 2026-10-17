---
doc: design
# Machine contract read by vpg.tools.design and vpg.tools.legibility.
palette: {ink: "#1A1F26", paper: "#FFFFFF", brand: "<fill: #RRGGBB>"}
text_pairs:             # the only foreground/background pairs text may use; each must pass WCAG contrast
  - {fg: paper, bg: brand, use: large}
  - {fg: ink, bg: paper, use: body}
fonts: ["<fill: family>"]
approved_assets:        # supplied logos and marks, used as delivered (sha256 recorded)
  - {path: "<fill: assets/brand/logo.png>", sha256: "<fill>", role: logo}
prohibited: ["font:<fill: e.g. Inter>", "color:<fill: e.g. #FFBF00>"]
---
# Design — <fill: film title>

> Access: written by N09 Visual System; read by N08, N10, N11, N13, N17, N18. Locked when Wei approves N09.

## Visual language
<fill: one sentence; e.g. minimal, bold caps, high contrast, one recurring signal line>

## Layout system
<fill: grid, margins, title-safe area, the caption band>

## Typography
<fill: families and roles (display, section title, label, body); minimum 22 px at 1080p>

## Color
<fill: tokens and their roles; the approved text pairs; colour is never the only cue>

## Composition
<fill: one focal point per frame; reading order; use of the whole frame>

## Illustration principles
<fill: when illustration is used, and how it animates to explain>

## Motion behavior
<fill: one brand-derived motion vocabulary; seams; ring and camera rules>

## Accessibility
<fill: WCAG targets, no flashing, captions>

## Legibility
<fill: size, contrast, dwell and density limits>

## Brand constraints
<fill: logo use, clear space, co-brand lockups>

## Approved and prohibited patterns
<fill: patterns to reuse, and patterns that are banned (with reasons)>
