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
import { TranslocoService } from '@jsverse/transloco';
import { Highlighter } from '../chat/highlight';
import { highlightCodeBlocks } from '../chat/markdown';
import { DiagramRenderer } from './diagram';
import { DocCitation } from './docs-models';

export interface DocLink {
  path: string;
  anchor: string | null;
}

/**
 * One rendered documentation page: sanitized HTML (from `renderDoc`), its Mermaid diagrams drawn
 * on top, code highlighted, and the two kinds of link the page can contain turned into events.
 */
@Component({
  selector: 'app-doc-content',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'markdown doc-content block',
    '(click)': 'onClick($event)',
    '(keydown)': 'onKey($event)',
  },
  template: `<div [innerHTML]="safe()"></div>`,
})
export class DocContentComponent {
  private readonly sanitizer = inject(DomSanitizer);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly highlighter = inject(Highlighter);
  private readonly diagrams = inject(DiagramRenderer);
  private readonly transloco = inject(TranslocoService);

  /** Already sanitized by `renderDoc` (DOMPurify). */
  readonly html = input.required<string>();
  /** Redraws the diagrams when the theme changes. */
  readonly dark = input(false);
  readonly citationOpened = output<DocCitation>();
  readonly pageOpened = output<DocLink>();

  protected readonly safe = computed<SafeHtml>(() =>
    this.sanitizer.bypassSecurityTrustHtml(this.html()),
  );

  constructor() {
    effect(() => {
      this.html();
      this.dark();
      untracked(() => void this.enhance());
    });
  }

  private async enhance(): Promise<void> {
    await Promise.resolve(); // let the new HTML reach the DOM first
    const root = this.host.nativeElement;
    await highlightCodeBlocks(root, this.highlighter);
    for (const figure of Array.from(root.querySelectorAll<HTMLElement>('figure.diagram'))) {
      const source = figure.dataset['diagram'];
      if (!source || !figure.isConnected) continue;
      try {
        const svg = await this.diagrams.render(source);
        if (!figure.isConnected) continue;
        figure.querySelector('.diagram-error')?.remove();
        figure.replaceChildren(svg);
        figure.setAttribute('role', 'img');
        figure.setAttribute('aria-label', this.transloco.translate('docs.diagram'));
      } catch {
        if (figure.querySelector('.diagram-error')) continue;
        const note = document.createElement('figcaption');
        note.className = 'diagram-error';
        note.textContent = this.transloco.translate('docs.diagramFailed');
        figure.append(note);
      }
    }
  }

  protected onClick(event: MouseEvent): void {
    const target = event.target as HTMLElement | null;
    const link = target?.closest<HTMLAnchorElement>('a[data-doc-path]');
    if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) {
      event.preventDefault();
      this.pageOpened.emit({
        path: link.dataset['docPath']!,
        anchor: link.dataset['docAnchor'] ?? null,
      });
      return;
    }
    this.openCitation(target);
  }

  protected onKey(event: KeyboardEvent): void {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (this.openCitation(event.target as HTMLElement | null)) event.preventDefault();
  }

  private openCitation(target: HTMLElement | null): boolean {
    const code = target?.closest<HTMLElement>('code.cite-ok');
    const data = code?.dataset;
    if (!data?.['file']) return false;
    this.citationOpened.emit({
      ref: data['ref'] ?? '',
      application: data['app'] ?? '',
      file: data['file'],
      start_line: Number(data['start']),
      end_line: Number(data['end']),
    });
    return true;
  }
}
