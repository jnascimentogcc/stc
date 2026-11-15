# Global UI SPEC

## 1. Purpose

This document is the normative source for the design system and the global user-experience patterns. It applies to all interfaces of the project, regardless of functional domain.

The pages, flows, data, permissions and business rules of each feature must be defined in separate feature specs. In case of conflict, this SPEC prevails on global UI decisions; the feature spec prevails only on the feature-specific behavior.

The visual language ("Modern Technical": blue and white, Inter, JetBrains Mono) follows `proposal-ui.md`, **only its system design**. The features that proposal describes (global search, versioning, multi-product, analytics, Git integration and so on) are not part of STC.

### Technology context

- Frontend: Angular, Tailwind CSS and SpartanNG.
- Use stable, supported versions, pinned in the project configuration and not in this document.
- Reuse the components and patterns offered by SpartanNG and the project's shared library first.
- Use Tailwind and design tokens for visual composition; avoid arbitrary values when an equivalent token exists.
- Keep domain components separate from primitive, reusable components.
- Fonts are self-hosted (`@fontsource`); the interface makes no request to a font CDN.

## 2. Experience principles

- **Clarity:** each screen must communicate purpose, state and primary action without relying on ornamentation.
- **Consistency:** equivalent patterns must look and behave equivalently.
- **Control:** important actions must present their consequences, progress and result explicitly.
- **Traceability:** states, timestamps, identifiers and actors must be legible when relevant to the task.
- **Accessibility:** the interface must work with keyboard, assistive technologies, zoom and reduced-motion preferences.
- **Progressive disclosure:** present the necessary information first and reveal additional detail on request.
- **Recovery:** errors must explain the problem in clear language and offer a safe action when possible.

Avoid promotional aesthetics, decorative gradients, illustrations without function, saturated colors without meaning, excessive shadows and overly rounded surfaces.

## 3. Visual identity

### 3.1 Base palette

| Name | Hex | Main use |
|---|---:|---|
| Blue 600 | `#2563EB` | Brand, primary action, links, focus |
| Blue 700 | `#1D4ED8` | Hover, active, info text |
| Slate 50 | `#F8FAFC` | Page background |
| Slate 100 | `#F1F5F9` | Sidebar and secondary surfaces |
| Slate 200 / 300 | `#E2E8F0` / `#CBD5E1` | Borders |
| Ink | `#172033` | Text |
| Slate 900 | `#0F172A` | Code blocks and dark background |

State colors are semantic. Never use color alone to convey meaning; combine color with text, icon or visual pattern.

### 3.2 Normative tokens

`frontend/src/styles/tokens.css` is a copy of this block; keep them in sync.

```css
:root {
  color-scheme: light;

  /* Surfaces */
  --bg: #f8fafc;
  --bg-2: #f1f5f9;
  --surface: #ffffff;
  --surface-2: #f1f5f9;
  --surface-muted: #e2e8f0;
  --sidebar: #f1f5f9;

  /* Borders */
  --border: #e2e8f0;
  --border-2: #cbd5e1;
  --border-strong: #94a3b8;

  /* Text */
  --text: #172033;
  --text-2: #475569;
  --text-3: #566579;
  --text-inverse: #ffffff;

  /* Brand and actions */
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --accent-soft: rgba(37, 99, 235, 0.08);
  --accent-soft-2: #dbeafe;

  /* Feedback (text and icon colors reach 4.5:1 on their own tinted background) */
  --success: #166534;
  --success-bg: rgba(22, 163, 74, 0.1);
  --success-border: rgba(22, 163, 74, 0.34);
  --warning: #92400e;
  --warning-bg: rgba(217, 119, 6, 0.1);
  --warning-border: rgba(217, 119, 6, 0.38);
  --danger: #b91c1c;
  --danger-bg: rgba(220, 38, 38, 0.08);
  --danger-border: rgba(220, 38, 38, 0.34);
  --info: #1d4ed8;
  --info-bg: rgba(37, 99, 235, 0.08);
  --info-border: rgba(37, 99, 235, 0.3);
  --deprecated: #64748b;

  /* Code blocks are dark in both themes */
  --code-bg: #0f172a;
  --code-fg: #e2e8f0;
  --code-border: #1e293b;
  --code-muted: #94a3b8;
  --code-warning: #fcd34d;
  --code-danger: #fca5a5;

  /* Typography */
  --sans: 'Inter Variable', 'Inter', system-ui, sans-serif;
  --mono: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace;
  --text-h1: 36px;
  --text-h2: 28px;
  --text-h3: 22px;
  --text-h4: 18px;
  --text-body: 16px;
  --text-small: 14px;
  --text-caption: 12px;
  --text-code: 13px;
  --leading-reading: 1.7;

  /* Spacing */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --space-12: 48px;
  --space-16: 64px;
  --space-20: 80px;

  /* Shape and depth */
  --radius-sm: 4px;
  --radius: 6px;
  --radius-input: 8px;
  --radius-lg: 10px;
  --radius-xl: 12px;
  --shadow: 0 1px 3px rgba(23, 32, 51, 0.08), 0 1px 2px rgba(23, 32, 51, 0.05);
  --shadow-md: 0 8px 24px rgba(23, 32, 51, 0.12), 0 2px 6px rgba(23, 32, 51, 0.07);

  /* Layout and motion */
  --content-max: 1360px;
  --header-height: 64px;
  --sidebar-width: 260px;
  --toc-width: 220px;
  --reading-max: 850px;
  --reading-padding: 48px;
  --transition: 180ms ease;
}

[data-theme='dark'] {
  color-scheme: dark;
  --bg: #0b1220;
  --bg-2: #0f172a;
  --surface: #131c31;
  --surface-2: #1a243b;
  --surface-muted: #243049;
  --sidebar: #0f172a;
  --border: rgba(226, 232, 240, 0.12);
  --border-2: rgba(226, 232, 240, 0.2);
  --border-strong: rgba(226, 232, 240, 0.34);
  --text: #e2e8f0;
  --text-2: #cbd5e1;
  --text-3: #94a3b8;
  --text-inverse: #0b1220;
  --accent: #60a5fa;
  --accent-hover: #93c5fd;
  --accent-soft: rgba(96, 165, 250, 0.12);
  --accent-soft-2: rgba(96, 165, 250, 0.2);
  --success: #4ade80;
  --success-bg: rgba(74, 222, 128, 0.14);
  --success-border: rgba(74, 222, 128, 0.34);
  --warning: #fbbf24;
  --warning-bg: rgba(251, 191, 36, 0.14);
  --warning-border: rgba(251, 191, 36, 0.36);
  --danger: #f87171;
  --danger-bg: rgba(248, 113, 113, 0.14);
  --danger-border: rgba(248, 113, 113, 0.36);
  --info: #60a5fa;
  --info-bg: rgba(96, 165, 250, 0.14);
  --info-border: rgba(96, 165, 250, 0.34);
  --deprecated: #94a3b8;
  --code-bg: #0b1220;
  --code-border: rgba(226, 232, 240, 0.12);
  --shadow: 0 1px 3px rgba(0, 0, 0, 0.32), 0 1px 2px rgba(0, 0, 0, 0.22);
  --shadow-md: 0 10px 30px rgba(0, 0, 0, 0.42), 0 2px 8px rgba(0, 0, 0, 0.24);
}
```

Implementations may map these tokens to Tailwind (`src/styles/spartan-theme.css` does, in the SpartanNG naming: helm `primary` is `--accent`, helm `accent` is `--accent-soft`), but the semantic names and values must remain the source of truth.

### 3.3 Contrast and approved deviations

Text and icon colors are measured against the surface they sit on (WCAG 2.2 AA: 4.5:1 for text, 3:1 for graphics). Where the proposal's values failed, this SPEC deliberately differs:

| Token | Proposal | This SPEC | Why |
|---|---|---|---|
| `--text-3` (muted text) | `#64748B` | `#566579` | The proposal's value is below 4.5:1 on the sidebar surface |
| `--success` (text) | `#16A34A` | `#166534` | Below 4.5:1 on its tinted background; the proposal's green stays in the background and border tokens |
| `--warning` (text) | `#D97706` | `#92400E` | Same reason |
| `--danger` (text) | `#DC2626` | `#B91C1C` | Same reason |
| Font weights | 600 | `650` allowed | Inter is the variable font, so intermediate weights work |

Dark theme text and feedback colors are lighter tints of the same hues and meet the same ratios.

## 4. Typography and content

| Role | Token | Use |
|---|---|---|
| Interface and chat text | `--text-small` (14px) | Controls, chat, lists, navigation; `line-height` 1.5 to 1.6 |
| Reading text | `--text-body` (16px) | **Only** the documentation reading area (`/docs` article), `line-height: var(--leading-reading)` (1.7) |
| Headings in the reading area | `--text-h1` 36px, `--text-h2` 28px, `--text-h3` 22px, `--text-h4` 18px | Inside `.doc-content`; elsewhere headings are no larger than 20px |
| Captions and labels | `--text-caption` (12px) | Meta information, breadcrumbs |
| Code | `--text-code` (13px) | Inline code and code blocks |

- Interface and content: `var(--sans)`.
- IDs, timestamps, code, metrics and tabular data: `var(--mono)`.
- Headings must not depend on size or color alone.
- Labels must be short, specific and close to the corresponding control.
- Avoid all-uppercase text, except in short labels, badges and compact headers (tree group names, "On this page").
- Messages must state what happened and, when applicable, what the user can do next.

## 5. Global base and layout

- Apply `box-sizing: border-box` globally and remove the default `body` margins.
- The `body` uses background `var(--bg)`, text `var(--text)`, `min-height: 100vh` and antialiasing.
- The main content has maximum width `var(--content-max)`, automatic margin and 16px horizontal padding; from 640px, use 24px.
- The application header is sticky, `var(--header-height)` (64px) tall.
- The documentation layout has three columns: sidebar (`--sidebar-width`, 260px, from `lg`), article (`--reading-max`, 850px, padding `--reading-padding`, 48px from `lg`) and "On this page" (`--toc-width`, 220px, from `xl`). Below `lg` the sidebar becomes a drawer; below `xl` the table of contents becomes a disclosure at the top of the article.
- Use grid or flexbox predictably, keeping the visual order consistent with the DOM order.
- The page header includes a title, an optional description and contextual actions; on narrow screens, actions may stack without losing hierarchy.
- Persistent navigation must clearly indicate the active item and remain keyboard accessible.
- Scrollbars may be discreet, but must not become invisible when they are needed to understand overflow.
- Respect safe areas on mobile devices when fixed elements exist.

## 6. Global components

### 6.1 Buttons

Allowed variants: `primary`, `secondary` (outline), `ghost` and `destructive`.

- There must be at most one primary action per action group.
- All buttons implement default, hover, active, focus-visible, disabled and loading states.
- Destructive buttons use `danger` tokens and require confirmation when the consequence is not easily reversible.
- Icon-only buttons need an accessible name and a tooltip when the meaning is not universal.
- The loading state keeps the button's width and prevents duplicate submissions.

### 6.2 Forms

- Each field has a visible label, unless an equally clear and accessible alternative exists.
- Helper text and errors must be programmatically associated with the field.
- Required states: default, hover, focus-visible, disabled, readonly, valid and invalid.
- Inputs use radius `var(--radius-input)`.
- Validate at the right moment: avoid errors before the first interaction, unless submission requires full validation.
- Preserve entered data after recoverable failures.
- Required fields must be identified by text or an explained symbol, never by color alone.
- Group related options with `fieldset` and `legend` where applicable.

### 6.3 Cards

- Use cards only to group related content and actions.
- Background `var(--surface)`, border `var(--border)`, radius `var(--radius-lg)` and shadow `var(--shadow)` when needed.
- Avoid nested cards without a clear semantic distinction.

### 6.4 Tables and data lists

- Use tables for data comparable by columns; use lists or cards when tabular comparison is not needed.
- Headers must be programmatically associated with cells.
- Align numbers consistently and use a monospaced font for identifiers and metrics.
- Offer horizontal scroll on narrow screens (inside the article, never on the page).
- Pagination, sorting, filters and selection must keep visible state and feedback.
- Empty, loading and error states occupy the table region without unexpectedly changing the layout.

### 6.5 Badges and states

- Variants: `neutral`, `success`, `warning`, `danger`, `info` and `deprecated` (struck through).
- Combine text with color; icons are optional and never replace the label.
- Radius `var(--radius)`; use badges for state or short categorization, not for actions.

### 6.6 Alerts

- Variants: `info`, `success`, `warning`, `danger`, each with an icon and text; the tinted background and border come from the feedback tokens, the text stays `--text`.
- Alerts that report a status of the page (outdated, incomplete) use `role="status"`; failures use `role="alert"`.

### 6.7 Modals, dialogs and drawers

- Use a modal only when the task requires immediate attention or contextual confirmation.
- Focus enters the dialog, stays contained while it is open and returns to the originating element on close.
- There must be an accessible title, a close action and defined behavior for `Escape`.
- Do not close on outside click when that could cause data loss or implicitly confirm an action.
- On mobile, drawers or full-screen dialogs may replace narrow modals when they improve the task (the documentation sidebar is one).

### 6.8 Tooltips, menus and popovers

- Essential content must never exist only in a tooltip.
- Actionable elements must work by keyboard and touch, not only by hover.
- Menus implement focus management, arrow keys and closing with `Escape`.

### 6.9 Documentation components

Used by the `/docs` viewer; they reuse the tokens above.

- **Breadcrumbs:** application, group, page; the current page is plain text with `aria-current="page"`, the others are links; separated by a chevron icon.
- **Page tree:** groups that expand and collapse (button with `aria-expanded`), the group of the current page open by default, every group open while filtering; the current page is highlighted (`--accent-soft-2`) and has `aria-current="page"`; rows are at least 36px tall.
- **Table of contents:** level 2 and 3 headings, the one being read highlighted by scroll position (`aria-current="location"`), clicking moves focus to the heading.
- **Code block:** dark in both themes (`--code-*` tokens), a bar with the language and a **Copy** button that announces "Copied" for two seconds; also used for the code behind a citation.
- **Previous / next:** two cards at the end of the article.
- **Tables** in the article scroll horizontally inside their own container.
- **Diagrams** (Mermaid) are drawn with the tokens and fall back to their source when they cannot be drawn.

## 7. Feedback and interface states

Each asynchronous surface must define, when applicable:

- **Loading:** visible progress, without blocking unrelated regions; use a skeleton only when it represents the real structure.
- **Empty:** explain why there is no data and offer an action when a relevant action exists.
- **Success:** confirm completion without unnecessarily interrupting the flow.
- **Warning:** explain the risk or incomplete condition before the consequential action.
- **Error:** present a clear message, preserve context and offer retry or recovery when possible.
- **Disabled:** explain the unavailability when it is not obvious.

Toasts are for transient feedback and must not contain information required to complete a task. Persistent errors must stay next to the affected region.

## 8. Responsiveness

Reference breakpoints, aligned with Tailwind:

| Name | Minimum width | Intent |
|---|---:|---|
| Base | `0px` | Mobile-first |
| `sm` | `640px` | Large phones and small tablets |
| `md` | `768px` | Tablets |
| `lg` | `1024px` | Desktop (documentation sidebar visible) |
| `xl` | `1280px` | Wide desktop (table of contents visible) |
| `2xl` | `1536px` | Very wide screens |

- Implement mobile-first and validate with real content, zoom and long strings.
- No essential action may depend exclusively on hover.
- Interactive targets should be at least 44×44 CSS pixels whenever possible; dense navigation lists may use 36px rows.
- Tables, code and wide content must handle overflow without squeezing text until it is illegible.
- Do not hide essential content merely to fit the layout; reorganize or offer progressive disclosure.

## 9. Accessibility

Minimum target: WCAG 2.2 level AA.

- Use semantic HTML before ARIA.
- Ensure full keyboard navigation, a logical focus order and a clearly perceivable `focus-visible` indicator (2px outline in `--accent`, offset 2px).
- Normal text must reach a minimum contrast of 4.5:1; large text and essential graphical elements, 3:1 (see 3.3).
- Controls must have an accessible name, role, value and state.
- Important asynchronous changes must be announced without improperly moving focus.
- Informative images need alternative text; decorative images must be ignored by assistive technologies.
- Respect `prefers-reduced-motion`; non-essential animations must be removed or reduced.
- Support zoom up to 200% without loss of content or functionality.
- Errors must be identified by text and associated with the corresponding fields.

## 10. Internationalization

- The default interface language is English (`en`); other locales are optional and added as translation files.
- All interface text must use the project's i18n mechanism; do not put final strings directly in templates or components.
- Do not concatenate fragments of translatable sentences.
- Support text expansion, pluralization, grammatical gender where applicable, and local date, time, number and currency formats.
- Do not assume fixed lengths for labels, buttons or values.
- Separate interface language, formatting locale and timezone.
- Prepare layout and directional icons for RTL languages if the product requirements include them.

## 11. Themes and preferences

- Light and dark themes must use the same semantic tokens and keep an equivalent hierarchy.
- The theme is chosen by the user (toggle in the header), saved in the browser, and follows the system preference when there is no saved choice.
- Theme changes must not cause loss of state or a prolonged flash of the wrong theme.
- Third-party components (Shiki, Mermaid) must be themed with the same tokens.

## 12. Presentation security

- HTML content coming from external sources must be sanitized before entering the DOM.
- External links opened in a new window must use `rel="noopener noreferrer"`.
- Do not expose stack traces, secrets, tokens or internal details in error messages.
- Potentially sensitive content must respect the visibility rules defined in the feature spec.

## 13. Global acceptance criteria

A UI implementation is only complete when it:

1. Uses SpartanNG components or shared components before creating new equivalents.
2. Uses design tokens and does not introduce arbitrary colors, typography or spacing without justification.
3. Implements the relevant loading, empty, success, warning, error and disabled states.
4. Works at the applicable breakpoints and without unintended overflow.
5. Is operable by keyboard and keeps focus visible and predictable.
6. Meets WCAG 2.2 AA in the flows covered.
7. Uses the i18n mechanism and supports expanded content and local formats.
8. Works in light and dark themes when both are provided.
9. Includes tests proportional to the risk and visual validation at the relevant sizes.
10. Documents any approved deviation from this SPEC.

## 14. Relationship with other artifacts

- **Feature spec:** defines pages, flows, data, permissions, content and specific criteria.
- **`proposal-ui.md`:** the design reference this SPEC was derived from; only its system design applies.
- **Design tokens:** materialize this SPEC's normative values in formats consumable by code.
- **Component library:** implements the reusable patterns.
- **UI engineering skill:** defines the procedure agents use to implement UI.
- **UI review skill:** defines the procedure agents use to validate conformance.

No skill may duplicate or replace the normative decisions of this document.
