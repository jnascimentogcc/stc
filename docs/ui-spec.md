# Global UI SPEC

## 1. Purpose

This document is the normative source for the design system and the global user-experience patterns. It applies to all interfaces of the project, regardless of functional domain.

The pages, flows, data, permissions and business rules of each feature must be defined in separate feature specs. In case of conflict, this SPEC prevails on global UI decisions; the feature spec prevails only on the feature-specific behavior.

### Technology context

- Frontend: Angular, Tailwind CSS and SpartanNG.
- Use stable, supported versions, pinned in the project configuration and not in this document.
- Reuse the components and patterns offered by SpartanNG and the project's shared library first.
- Use Tailwind and design tokens for visual composition; avoid arbitrary values when an equivalent token exists.
- Keep domain components separate from primitive, reusable components.

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
| Pearl Bush | `#ECE6DA` | Light background and neutral context |
| Foggy Gray | `#CACEB5` | Secondary surfaces and subtle states |
| Nandor | `#4E615B` | Brand, primary action and positive states |
| Ebony Clay | `#2F3543` | Strong text and dark surfaces |
| Ebony Clay Dark | `#1F2532` | Dark background and maximum contrast |

State colors are semantic. Never use color alone to convey meaning; combine color with text, icon or visual pattern.

### 3.2 Normative tokens

```css
:root {
  color-scheme: light;

  /* Palette */
  --pearl-bush: #ece6da;
  --foggy-gray: #caceb5;
  --nandor: #4e615b;
  --ebony-clay: #2f3543;
  --ebony-clay-dark: #1f2532;

  /* Surfaces */
  --bg: #ece6da;
  --bg-2: #e4ddcf;
  --surface: #f8f5ee;
  --surface-2: #f1ece2;
  --surface-muted: #caceb5;

  /* Borders */
  --border: rgba(47, 53, 67, 0.14);
  --border-2: rgba(47, 53, 67, 0.24);
  --border-strong: rgba(47, 53, 67, 0.36);

  /* Text */
  --text: #1f2532;
  --text-2: #4e615b;
  --text-3: rgba(31, 37, 50, 0.58);
  --text-inverse: #ece6da;

  /* Brand and actions */
  --accent: #4e615b;
  --accent-hover: #43544f;
  --accent-soft: rgba(78, 97, 91, 0.12);
  --accent-soft-2: rgba(78, 97, 91, 0.18);

  /* Feedback */
  --success: #4e615b;
  --success-bg: rgba(78, 97, 91, 0.12);
  --success-border: rgba(78, 97, 91, 0.28);
  --warning: #b7791f;
  --warning-bg: rgba(183, 121, 31, 0.13);
  --warning-border: rgba(183, 121, 31, 0.30);
  --danger: #b94a3a;
  --danger-bg: rgba(185, 74, 58, 0.12);
  --danger-border: rgba(185, 74, 58, 0.30);
  --info: #3f647c;
  --info-bg: rgba(63, 100, 124, 0.12);
  --info-border: rgba(63, 100, 124, 0.30);

  /* Typography */
  --sans: "DM Sans", "Inter", system-ui, sans-serif;
  --mono: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;

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

  /* Shape and depth */
  --radius-sm: 4px;
  --radius: 6px;
  --radius-lg: 10px;
  --radius-xl: 14px;
  --shadow: 0 1px 3px rgba(31, 37, 50, 0.08), 0 1px 2px rgba(31, 37, 50, 0.05);
  --shadow-md: 0 8px 24px rgba(31, 37, 50, 0.12), 0 2px 6px rgba(31, 37, 50, 0.07);

  /* Layout and motion */
  --content-max: 1360px;
  --header-height: 56px;
  --transition: 180ms ease;
}

[data-theme="dark"] {
  color-scheme: dark;
  --bg: #1f2532;
  --bg-2: #252c3a;
  --surface: #2f3543;
  --surface-2: #363d4d;
  --surface-muted: rgba(202, 206, 181, 0.12);
  --border: rgba(236, 230, 218, 0.12);
  --border-2: rgba(236, 230, 218, 0.20);
  --border-strong: rgba(236, 230, 218, 0.34);
  --text: #ece6da;
  --text-2: #caceb5;
  --text-3: rgba(236, 230, 218, 0.58);
  --text-inverse: #1f2532;
  --accent: #caceb5;
  --accent-hover: #d8dbc7;
  --accent-soft: rgba(202, 206, 181, 0.13);
  --accent-soft-2: rgba(202, 206, 181, 0.20);
  --success: #caceb5;
  --success-bg: rgba(202, 206, 181, 0.13);
  --success-border: rgba(202, 206, 181, 0.30);
  --warning: #d49a3a;
  --warning-bg: rgba(212, 154, 58, 0.14);
  --warning-border: rgba(212, 154, 58, 0.32);
  --danger: #e06a5b;
  --danger-bg: rgba(224, 106, 91, 0.14);
  --danger-border: rgba(224, 106, 91, 0.34);
  --info: #8eaec1;
  --info-bg: rgba(142, 174, 193, 0.14);
  --info-border: rgba(142, 174, 193, 0.34);
  --shadow: 0 1px 3px rgba(0, 0, 0, 0.32), 0 1px 2px rgba(0, 0, 0, 0.22);
  --shadow-md: 0 10px 30px rgba(0, 0, 0, 0.42), 0 2px 8px rgba(0, 0, 0, 0.24);
}
```

Implementations may map these tokens to Tailwind, but the semantic names and values must remain the source of truth.

## 4. Typography and content

- Base: `14px`, `line-height` between `1.5` and `1.6` for running text.
- Interface and content: `var(--sans)`.
- IDs, timestamps, code, metrics and tabular data: `var(--mono)`.
- Use a limited, hierarchical type scale; headings must not depend on size or color alone.
- Labels must be short, specific and close to the corresponding control.
- Avoid all-uppercase text, except in short labels, badges and compact headers.
- Messages must state what happened and, when applicable, what the user can do next.

## 5. Global base and layout

- Apply `box-sizing: border-box` globally and remove the default `body` margins.
- The `body` uses background `var(--bg)`, text `var(--text)`, `min-height: 100vh` and antialiasing.
- The main content has maximum width `var(--content-max)`, automatic margin and 16px horizontal padding; from 640px, use 24px.
- Use grid or flexbox predictably, keeping the visual order consistent with the DOM order.
- The page header includes a title, an optional description and contextual actions; on narrow screens, actions may stack without losing hierarchy.
- Persistent navigation must clearly indicate the active item and remain keyboard accessible.
- Scrollbars may be discreet, but must not become invisible when they are needed to understand overflow.
- Respect safe areas on mobile devices when fixed elements exist.

## 6. Global components

### 6.1 Buttons

Allowed variants: `primary`, `secondary`, `ghost` and `destructive`.

- There must be at most one primary action per action group.
- All buttons implement default, hover, active, focus-visible, disabled and loading states.
- Destructive buttons use `danger` tokens and require confirmation when the consequence is not easily reversible.
- Icon-only buttons need an accessible name and a tooltip when the meaning is not universal.
- The loading state keeps the button's width and prevents duplicate submissions.

### 6.2 Forms

- Each field has a visible label, unless an equally clear and accessible alternative exists.
- Helper text and errors must be programmatically associated with the field.
- Required states: default, hover, focus-visible, disabled, readonly, valid and invalid.
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
- Offer horizontal scroll on narrow screens or a responsive representation defined in the feature spec.
- Pagination, sorting, filters and selection must keep visible state and feedback.
- Empty, loading and error states occupy the table region without unexpectedly changing the layout.

### 6.5 Badges and states

- Variants: `neutral`, `success`, `warning`, `danger` and `info`.
- Combine text with color; icons are optional and never replace the label.
- Use badges for state or short categorization, not for actions.

### 6.6 Modals, dialogs and drawers

- Use a modal only when the task requires immediate attention or contextual confirmation.
- Focus enters the dialog, stays contained while it is open and returns to the originating element on close.
- There must be an accessible title, a close action and defined behavior for `Escape`.
- Do not close on outside click when that could cause data loss or implicitly confirm an action.
- On mobile, drawers or full-screen dialogs may replace narrow modals when they improve the task.

### 6.7 Tooltips, menus and popovers

- Essential content must never exist only in a tooltip.
- Actionable elements must work by keyboard and touch, not only by hover.
- Menus implement focus management, arrow keys and closing with `Escape`.

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
| `lg` | `1024px` | Desktop |
| `xl` | `1280px` | Wide desktop |
| `2xl` | `1536px` | Very wide screens |

- Implement mobile-first and validate with real content, zoom and long strings.
- No essential action may depend exclusively on hover.
- Interactive targets should be at least 44×44 CSS pixels whenever possible.
- Tables, code and wide content must handle overflow without squeezing text until it is illegible.
- Do not hide essential content merely to fit the layout; reorganize or offer progressive disclosure.

## 9. Accessibility

Minimum target: WCAG 2.2 level AA.

- Use semantic HTML before ARIA.
- Ensure full keyboard navigation, a logical focus order and a clearly perceivable `focus-visible` indicator.
- Normal text must reach a minimum contrast of 4.5:1; large text and essential graphical elements, 3:1.
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
- The strategy for selecting and persisting the theme must be defined by the project; the initial state must respect the system preference when there is no saved choice.
- Theme changes must not cause loss of state or a prolonged flash of the wrong theme.
- Third-party components must be themed with the same tokens.

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
- **Design tokens:** materialize this SPEC's normative values in formats consumable by code.
- **Component library:** implements the reusable patterns.
- **UI engineering skill:** defines the procedure agents use to implement UI.
- **UI review skill:** defines the procedure agents use to validate conformance.

No skill may duplicate or replace the normative decisions of this document.
