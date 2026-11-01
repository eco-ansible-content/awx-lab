---
name: AWX Lab website
description: A practical handbook extending the cyan AWX LAB terminal identity.
colors:
  paper: "#f5f6f2"
  white: "#fff"
  ink: "#173231"
  muted: "#526563"
  line: "#d4dcd6"
  accent: "#006b64"
  bright: "#71e6dc"
  dark: "#10161c"
  code: "#e9eeea"
  code-ink: "#dfe9ec"
  code-label: "#a4b8c3"
  accent-hover: "#084c48"
  copy-background: "#26363d"
  copy-ink: "#daeaee"
  copy-border: "#475b62"
  copy-hover: "#38505a"
  input-border: "#8ca398"
  selector-hover: "#e7eee8"
typography:
  display:
    fontFamily: '"DM Sans", sans-serif'
    fontSize: "clamp(2.8rem, 4.7vw, 4.1rem)"
    fontWeight: 600
    lineHeight: 1.08
    letterSpacing: "-0.04em"
  document-title:
    fontFamily: '"DM Sans", sans-serif'
    fontSize: "clamp(2.5rem, 4vw, 3.5rem)"
    fontWeight: 600
    lineHeight: 1.12
    letterSpacing: "-0.04em"
  headline:
    fontFamily: '"DM Sans", sans-serif'
    fontSize: "clamp(2rem, 3.3vw, 2.8rem)"
    fontWeight: 600
    lineHeight: 1.12
    letterSpacing: "-0.035em"
  title:
    fontFamily: '"DM Sans", sans-serif'
    fontSize: "1.2rem"
    fontWeight: 600
    lineHeight: 1.12
    letterSpacing: "-0.015em"
  body:
    fontFamily: '"DM Sans", sans-serif'
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.65
  prose:
    fontFamily: '"DM Sans", sans-serif'
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.8
  label:
    fontFamily: '"DM Sans", sans-serif'
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.65
  mono:
    fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace'
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.8
rounded:
  inline-code: "3px"
  small-control: "4px"
  input: "5px"
  button: "6px"
  code-panel: "8px"
  terminal-preview: "10px"
spacing:
  compact: "8px"
  small: "12px"
  text: "20px"
  group: "24px"
  wide-group: "32px"
  desktop-gutter: "48px"
  section: "64px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.white}"
    typography: "{typography.label}"
    rounded: "{rounded.button}"
    padding: "13px 20px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  text-link:
    textColor: "{colors.accent}"
    typography: "{typography.label}"
  source-choice:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    padding: "13px 14px"
  source-choice-selected:
    textColor: "{colors.accent}"
  copy-button:
    backgroundColor: "{colors.copy-background}"
    textColor: "{colors.copy-ink}"
    rounded: "{rounded.small-control}"
    padding: "4px 9px"
  command-search:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.input}"
    padding: "12px 15px"
    width: "100%"
  document-navigation:
    textColor: "{colors.muted}"
  code-block:
    backgroundColor: "{colors.dark}"
    textColor: "{colors.code-ink}"
    typography: "{typography.mono}"
    rounded: "{rounded.code-panel}"
    padding: "48px 22px 22px"
  disclosure:
    textColor: "{colors.ink}"
    padding: "18px 28px 18px 0"
---

# Design System: AWX Lab website

## Overview

**Creative North Star: "The AWX Lab handbook"**

The website extends the terminal's cyan AWX LAB identity into a quiet, practical reading surface. Off-white paper, dark green text and measured teal accents frame real commands and illustrative captures of the actual console. Large introductory type gives way to compact reference material with clear section boundaries.

This document describes the implemented website only. Its sources are `assets/style.css`, `index.html`, `assets/site.js` and `build.mjs`; the repository-root `DESIGN.md` continues to own the native terminal. Frontmatter records implemented values, not an additional stylesheet. The sidecar adds component previews and metadata; its generated tonal ramps are preview aids, not new application colors.

**Key Characteristics:**

- Readable off-white pages with restrained teal emphasis.
- Self-hosted DM Sans paired with system monospace.
- Flat rules and spacing, with dark panels reserved for technical output.
- Sticky desktop navigation and a single-column mobile reading flow.
- Useful progressive enhancements over readable HTML.

## Colors

The palette translates cyan terminal identity into darker teal for legible links on paper, retaining bright cyan within dark output.

### Primary

- **Deep teal (`accent`):** links, primary action, selected source, current navigation and focus outlines. `accent-hover` darkens the primary button on hover.
- **Terminal cyan (`bright`):** successful result text and focus indicators within dark code areas.

### Neutral

- **Soft paper (`paper`):** the page and sticky header; white is used selectively for keycaps and the skip link.
- **Green ink (`ink`) / muted green (`muted`):** primary reading text versus supporting explanations, captions and navigation.
- **Quiet divider (`line`):** section, row and navigation separators.
- **Terminal charcoal (`dark`):** code, result output and terminal preview framing. Pale `code-ink` and `code-label` provide the corresponding text hierarchy.
- **Inline code wash (`code`):** small code references embedded in prose. Copy and input tokens describe their respective control surfaces and borders.

**The Surface Contrast Rule.** Use deep teal on the reading surface and bright cyan inside dark output; retain text labels and state attributes alongside color.

## Typography

DM Sans is self-hosted at `assets/fonts/dm-sans.ttf` as a variable font (weights 100–1000) with `font-display: swap`. Headings and prose share this family. Commands, keycaps, version labels and terminal framing use the system monospace stack in the frontmatter.

The display, document-title and headline roles capture desktop sizing. Section headings use balanced wrapping, compact line height and slight negative tracking. Body text is open and unhurried; labels remain sentence case. The brand is a compact typographic AWX/LAB lockup, with heavier AWX and a fine vertical divider before LAB.

Reference prose is capped at 76ch inside a 900px column; introductory prose commonly uses 38–66ch. Generated document headings use a smaller hierarchy (h2 1.9rem, h3 1.3rem, h4 1.1rem). Code is horizontally scrollable and keeps its source whitespace. Inline code can wrap long strings.

## Layout

The shared outer container is 1400px with 48px desktop side gutters. The overview pairs a 195px section rail with the reading column across a 65px gap. Generated documentation uses a 220px rail and a 75px gap. The header is sticky at the viewport top; overview and document rails begin at 115px and 105px respectively. Document navigation can scroll within `calc(100vh - 130px)`.

Sections use generous vertical separation: 64px padding and 64px following margin on the overview, with thin rules. The hero is a two-column 0.85fr/1.15fr arrangement with a real terminal preview; supporting examples, explanations and output use local grids. Text spacing is not governed by a single mathematical scale: retain the observed component values.

| Viewport | Implemented behavior |
| --- | --- |
| 1150px and below | Gutters reduce to 32px; rails and grid gaps narrow; installation and results become single columns. |
| 800px and below | Gutters become 24px; hero and page layouts become single columns; rails become static, wrapping navigation. Document contents start collapsed and can be expanded. Prose drops to 14px. |
| 500px and below | Gutters become 20px; header navigation takes its own row; console detail and footer stack; document contents use one column; code reduces to 11px. |

Hero titles use explicit responsive sizes (3.25rem at 1150px, 3.5rem at 800px, 3rem at 500px). Document titles become 2.8rem and 2.4rem at the latter two breakpoints. Long code and reference tables scroll within their own containers instead of widening the page.

## Elevation & Depth

The website uses no box shadows, gradients or blurred surfaces. Thin borders, whitespace and the contrast between paper and charcoal establish hierarchy. The sticky header is an opaque continuation of the page with a bottom rule. Motion is limited to smooth anchor scrolling and a 0.2s primary-button background transition; reduced-motion preferences disable both animation/transitions and smooth scrolling.

## Shapes

Controls have modest rounded corners, with the distinct radii captured in frontmatter. Inline code and keycaps are compact; code panels and screenshots have slightly softer edges. The terminal frame has rounded outer corners and a fine title-bar divider. Sections, tables, source choices and disclosures rely on straight horizontal rules rather than card shells. Icons are simple unfilled strokes with rounded caps and joins.

## Components

- **Primary action and text links:** the filled teal action has white text, a trailing arrow, a 6px corner radius and a darker hover state. Secondary actions are underlined text links. Focus uses a 3px teal outline offset by 5px; dark surfaces substitute terminal cyan.
- **Source selection:** a wrapping group of ordinary buttons with `aria-pressed`, a selected teal label and 2px underline, and a quiet hover wash. Minimum height is 48px. JavaScript initially selects Local changes and displays its matching example; without JavaScript all examples remain readable. These are buttons, not ARIA tabs.
- **Code and copy:** dark, rounded, horizontally scrollable blocks reserve room for a top-right copy control. Labels sit above code when present. JavaScript makes code focusable and adds Copy when the Clipboard API exists. Success temporarily reads Copied; failure selects the code and explains manual copying through a live status region. Feedback resets after 2200ms. The code focus outline is inset cyan.
- **Command search and tables:** a visible label precedes a transparent field with a thin green border, limited to 500px. Filtering matches command and purpose text case-insensitively; a live region announces the count and a plain message explains zero matches. The field starts disabled until JavaScript initializes. Tables use lightweight row rules, smaller muted headers and inline monospace commands.
- **Navigation:** the persistent header carries the wordmark, version and core links. The overview rail underlines the current section; generated-document navigation uses a heavier teal current-page link. “On this page” is a native details disclosure, opened on desktop initialization above 800px and left collapsed on mobile. Heading anchors become visible on hover or keyboard focus.
- **Disclosures:** native details/summary elements use top and bottom rules, generous summary padding and the browser disclosure marker. Installation and update details remain operable without JavaScript.
- **Terminal previews:** use captures of the actual interface with descriptive alternative text, explicit illustrative captions and a link to enlarge. The dark frame supports the image instead of replacing it with a simulated product UI.
- **Keycaps and reading links:** compact bordered keycaps clarify console shortcuts. Further-reading links form divided rows with a title and supporting description, stacking on narrow screens.

## Do's and Don'ts

### Do:

- Do preserve the AWX/LAB wordmark relationship and teal-to-cyan connection to the terminal.
- Do keep documentation and command examples readable without JavaScript.
- Do use existing flat rules, whitespace and typography to organize reading.
- Do preserve visible focus, semantic state labels and scrollable code/table regions.
- Do label terminal previews as illustrative captures of the actual interface.

### Don't:

- Don't apply website font or RGB tokens to the terminal-owned design.
- Don't turn reading sections into elevated cards or add decorative gradients.
- Don't hide supporting examples before progressive enhancement initializes.
- Don't remove captions or present illustrative terminal output as validation evidence.
