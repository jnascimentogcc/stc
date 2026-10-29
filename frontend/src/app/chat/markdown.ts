import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  output,
  untracked,
} from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import { Citation } from '../api/models';
import { Highlighter, languageForFence } from './highlight';

// Links in an answer open in a new tab without access to this page (ui-spec §12).
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A' && node.hasAttribute('href')) {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

/** Markdown to HTML that is always sanitized: an answer is untrusted text (it quotes code). */
export function renderMarkdown(source: string): string {
  const html = marked.parse(source, { async: false, gfm: true }) as string;
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, ADD_ATTR: ['target'] });
}

/**
 * Marks the inline code that is exactly a citation of the answer: a verified one becomes a
 * button that opens the cited lines, an unverified one is flagged. Works on HTML that was
 * already sanitized and only adds attributes this app chose itself.
 */
export function linkReferences(html: string, citations: Citation[]): string {
  if (citations.length === 0 || typeof DOMParser === 'undefined') return html;
  const byRef = new Map(citations.map((c) => [c.ref, c]));
  const document = new DOMParser().parseFromString(html, 'text/html');
  for (const code of Array.from(document.body.querySelectorAll('code'))) {
    if (code.closest('pre')) continue;
    const citation = byRef.get((code.textContent ?? '').trim());
    if (!citation) continue;
    code.classList.add('cite', citation.verified ? 'cite-ok' : 'cite-bad');
    code.dataset['ref'] = citation.ref;
    if (citation.verified) {
      code.setAttribute('role', 'button');
      code.setAttribute('tabindex', '0');
      code.setAttribute('aria-label', `${citation.ref}: show the code`);
    } else {
      code.setAttribute('aria-label', `${citation.ref} (unverified)`);
      code.setAttribute('title', 'Not verified against the code retrieved for this answer');
    }
  }
  return document.body.innerHTML;
}

@Component({
  selector: 'app-markdown',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'markdown block',
    '(click)': 'onActivate($event)',
    '(keydown)': 'onKey($event)',
  },
  template: `<div [innerHTML]="html()"></div>`,
})
export class MarkdownComponent {
  private readonly sanitizer = inject(DomSanitizer);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly highlighter = inject(Highlighter);

  readonly source = input.required<string>();
  /** The sources of the answer: references to them in the text become links. */
  readonly citations = input<Citation[]>([]);
  /** Highlight fenced code once the text is final (not while it is still being streamed). */
  readonly highlight = input(false);
  /** A verified reference in the text was activated. */
  readonly referenceOpened = output<string>();

  // The HTML was sanitized by DOMPurify, so Angular's own (stricter) pass is not applied again.
  protected readonly html = computed<SafeHtml>(() =>
    this.sanitizer.bypassSecurityTrustHtml(
      linkReferences(renderMarkdown(this.source()), this.citations()),
    ),
  );

  constructor() {
    effect(() => {
      this.html();
      if (this.highlight()) untracked(() => void this.highlightBlocks());
    });
  }

  private async highlightBlocks(): Promise<void> {
    await Promise.resolve(); // let the new HTML reach the DOM first
    for (const code of Array.from(this.host.nativeElement.querySelectorAll('pre > code'))) {
      const fence = Array.from(code.classList).find((c) => c.startsWith('language-'));
      const language = fence ? languageForFence(fence.slice('language-'.length)) : null;
      const text = code.textContent ?? '';
      const lines = await this.highlighter.tokens(text.replace(/\n$/, ''), language);
      if (!lines || code.textContent !== text) continue;
      // Built from text nodes: a token can never become markup.
      code.replaceChildren(
        ...lines.flatMap((line, index) => [
          ...line.map((token) => {
            const span = document.createElement('span');
            span.textContent = token.text;
            if (token.color) span.style.color = token.color;
            if (token.italic) span.style.fontStyle = 'italic';
            if (token.bold) span.style.fontWeight = '700';
            return span;
          }),
          document.createTextNode(index < lines.length - 1 ? '\n' : ''),
        ]),
      );
    }
  }

  protected onActivate(event: Event): void {
    this.open(event.target);
  }

  protected onKey(event: KeyboardEvent): void {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (this.open(event.target)) event.preventDefault();
  }

  private open(target: EventTarget | null): boolean {
    const reference = (target as HTMLElement | null)?.closest?.<HTMLElement>('code.cite-ok');
    const ref = reference?.dataset['ref'];
    if (!ref) return false;
    this.referenceOpened.emit(ref);
    return true;
  }
}
