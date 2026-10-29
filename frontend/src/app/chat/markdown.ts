import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import DOMPurify from 'dompurify';
import { marked } from 'marked';

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

@Component({
  selector: 'app-markdown',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'markdown block' },
  template: `<div [innerHTML]="html()"></div>`,
})
export class MarkdownComponent {
  private readonly sanitizer = inject(DomSanitizer);
  readonly source = input.required<string>();

  // The HTML was sanitized by DOMPurify, so Angular's own (stricter) pass is not applied again.
  protected readonly html = computed<SafeHtml>(() =>
    this.sanitizer.bypassSecurityTrustHtml(renderMarkdown(this.source())),
  );
}
