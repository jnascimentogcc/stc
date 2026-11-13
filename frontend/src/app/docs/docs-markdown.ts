import { renderMarkdown } from '../chat/markdown';
import { DocHeading } from './docs-models';

export interface RenderedDoc {
  html: string;
  headings: DocHeading[];
}

export interface RenderContext {
  /** The application whose page this is. */
  application: string;
  /** Path of the page inside the application's documentation, e.g. `features/job-x.md`. */
  path: string;
  /** Names of the applications that have documentation, to tell `other-app/file.py:1-2` apart. */
  applications: string[];
}

const CITATION = /^([\w@./+-]+):(\d+)(?:-(\d+))?$/;
const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

function slug(text: string): string {
  const base = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `h-${base || 'section'}`;
}

/**
 * Resolves a link written in a page against the page's own path. Returns the target page path,
 * or null when it is not a link to another page of the documentation (an external URL, an
 * absolute path, or something that is not markdown).
 */
export function resolvePagePath(from: string, file: string): string | null {
  if (!file || file.startsWith('/') || SCHEME.test(file) || !file.toLowerCase().endsWith('.md')) {
    return null;
  }
  const parts = from.split('/').slice(0, -1);
  for (const part of file.split('/')) {
    if (part === '..') {
      if (parts.length === 0) return null;
      parts.pop();
    } else if (part !== '.' && part !== '') {
      parts.push(part);
    }
  }
  return parts.join('/');
}

/**
 * Markdown of a documentation page to sanitized HTML, with what the viewer needs added on top
 * (only attributes this app chooses): anchors on headings, links between pages, `file:lines`
 * references that open the code, and Mermaid blocks marked for rendering.
 */
export function renderDoc(markdown: string, context: RenderContext): RenderedDoc {
  const document = new DOMParser().parseFromString(renderMarkdown(markdown), 'text/html');
  const headings: DocHeading[] = [];
  const used = new Set<string>();

  for (const element of Array.from(document.body.querySelectorAll('h1, h2, h3, h4'))) {
    const text = (element.textContent ?? '').trim();
    let id = slug(text);
    for (let n = 2; used.has(id); n++) id = `${slug(text)}-${n}`;
    used.add(id);
    element.id = id;
    headings.push({ id, text, level: Number(element.tagName.slice(1)) });
  }

  for (const link of Array.from(document.body.querySelectorAll('a[href]'))) {
    const [file, anchor] = (link.getAttribute('href') ?? '').split('#', 2);
    const target = resolvePagePath(context.path, file);
    if (target === null) continue;
    const query = new URLSearchParams({ page: target });
    link.setAttribute('href', `/docs/${encodeURIComponent(context.application)}?${query}`);
    link.removeAttribute('target');
    link.removeAttribute('rel');
    link.setAttribute('data-doc-path', target);
    if (anchor) link.setAttribute('data-doc-anchor', anchor);
  }

  for (const code of Array.from(document.body.querySelectorAll('code'))) {
    if (code.closest('pre')) continue;
    const match = CITATION.exec((code.textContent ?? '').trim());
    if (!match || !/[./]/.test(match[1])) continue;
    const ref = match[0];
    const start = Number(match[2]);
    const end = match[3] ? Number(match[3]) : start;
    let application = context.application;
    let file = match[1];
    const slash = file.indexOf('/');
    if (slash > 0) {
      const first = file.slice(0, slash);
      if (first !== application && context.applications.includes(first)) {
        application = first;
        file = file.slice(slash + 1);
      }
    }
    if (end < start) continue;
    code.classList.add('cite', 'cite-ok');
    code.setAttribute('role', 'button');
    code.setAttribute('tabindex', '0');
    code.setAttribute('aria-label', `${ref}: show the code`);
    code.dataset['app'] = application;
    code.dataset['file'] = file;
    code.dataset['start'] = String(start);
    code.dataset['end'] = String(end);
    code.dataset['ref'] = ref;
  }

  for (const code of Array.from(document.body.querySelectorAll('pre > code.language-mermaid'))) {
    const pre = code.parentElement!;
    const figure = document.createElement('figure');
    figure.className = 'diagram';
    figure.dataset['diagram'] = code.textContent ?? '';
    pre.replaceWith(figure);
    figure.append(pre);
  }

  return { html: document.body.innerHTML, headings };
}
