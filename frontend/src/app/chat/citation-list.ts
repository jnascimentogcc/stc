import { ChangeDetectionStrategy, Component, inject, input, signal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideCircleAlert, lucideCircleCheck } from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { ChatApi } from '../api/chat-api';
import { Citation, FileSlice } from '../api/models';

interface Opened {
  state: 'loading' | 'ready' | 'error';
  slice?: FileSlice;
  lines?: { number: number; text: string }[];
  error?: string;
}

/** The sources of an answer. A verified one opens the lines it points to (QRY-06). */
@Component({
  selector: 'app-citation-list',
  imports: [NgIcon, TranslocoDirective],
  providers: [provideIcons({ lucideCircleAlert, lucideCircleCheck })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ng-container *transloco="let t">
      @if (citations().length > 0) {
        <section [attr.aria-label]="t('chat.sources')" class="mt-3">
          <h3 class="mb-1.5 text-xs font-medium text-text-3">{{ t('chat.sources') }}</h3>
          <ul class="flex flex-wrap gap-1.5">
            @for (citation of citations(); track citation.ref) {
              <li>
                @if (citation.verified) {
                  <button
                    type="button"
                    class="inline-flex min-h-8 items-center gap-1.5 rounded-md border border-success-border bg-success-bg px-2 font-mono text-xs text-foreground hover:bg-accent-soft-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                    [attr.aria-expanded]="isOpen(citation)"
                    (click)="toggle(citation)"
                  >
                    <ng-icon name="lucideCircleCheck" class="text-success" aria-hidden="true" />
                    <span>{{ citation.ref }}</span>
                    <span class="sr-only">{{ t('chat.verified') }}</span>
                  </button>
                } @else {
                  <span
                    class="inline-flex min-h-8 items-center gap-1.5 rounded-md border border-warning-border bg-warning-bg px-2 font-mono text-xs text-foreground"
                  >
                    <ng-icon name="lucideCircleAlert" class="text-warning" aria-hidden="true" />
                    <span>{{ citation.ref }}</span>
                    <span class="font-sans">{{ t('chat.unverified') }}</span>
                  </span>
                }
              </li>
            }
          </ul>
          @for (citation of citations(); track citation.ref) {
            @if (isOpen(citation)) {
              <div
                class="mt-2 overflow-hidden rounded-md border border-border bg-surface-2"
                role="region"
                [attr.aria-label]="citation.ref"
              >
                @switch (opened()[citation.ref]?.state) {
                  @case ('loading') {
                    <p class="p-3 text-text-3">{{ t('chat.loadingCode') }}</p>
                  }
                  @case ('error') {
                    <p class="p-3 text-danger" role="alert">{{ opened()[citation.ref]?.error }}</p>
                  }
                  @case ('ready') {
                    <div
                      class="flex flex-wrap items-center gap-2 border-b border-border px-3 py-1.5 text-xs text-text-3"
                    >
                      <span class="font-mono">{{ citation.ref }}</span>
                      @if (opened()[citation.ref]?.slice?.source?.code_version; as version) {
                        <span>{{ t('chat.codeVersion') }}</span>
                        <span class="font-mono">{{ version.slice(0, 7) }}</span>
                      }
                      @if (opened()[citation.ref]?.slice?.stale) {
                        <span class="text-warning">{{ t('chat.fileChanged') }}</span>
                      }
                    </div>
                    <pre
                      class="overflow-x-auto p-3 font-mono text-xs leading-relaxed"
                      tabindex="0"
                    ><code>@for (line of opened()[citation.ref]?.lines; track line.number) {<span class="mr-3 inline-block w-8 select-none text-right text-text-3">{{ line.number }}</span>{{ line.text }}
}</code></pre>
                  }
                }
              </div>
            }
          }
        </section>
      }
    </ng-container>
  `,
})
export class CitationListComponent {
  private readonly api = inject(ChatApi);

  readonly citations = input.required<Citation[]>();
  protected readonly opened = signal<Record<string, Opened | undefined>>({});
  private readonly shown = signal<Set<string>>(new Set());

  protected isOpen(citation: Citation): boolean {
    return this.shown().has(citation.ref);
  }

  protected async toggle(citation: Citation): Promise<void> {
    const next = new Set(this.shown());
    if (next.has(citation.ref)) {
      next.delete(citation.ref);
      this.shown.set(next);
      return;
    }
    next.add(citation.ref);
    this.shown.set(next);
    if (this.opened()[citation.ref]?.state === 'ready') return;
    if (!citation.application || !citation.file || !citation.start_line || !citation.end_line) {
      return;
    }
    this.set(citation.ref, { state: 'loading' });
    try {
      const slice = await this.api.fileSlice(
        citation.application,
        citation.file,
        citation.start_line,
        citation.end_line,
      );
      const first = slice.source.start_line;
      this.set(citation.ref, {
        state: 'ready',
        slice,
        lines: slice.text.split('\n').map((text, index) => ({ number: first + index, text })),
      });
    } catch (error) {
      this.set(citation.ref, {
        state: 'error',
        error: error instanceof Error ? error.message : 'The code could not be loaded.',
      });
    }
  }

  private set(ref: string, value: Opened): void {
    this.opened.update((current) => ({ ...current, [ref]: value }));
  }
}
