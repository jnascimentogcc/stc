import {
  ChangeDetectionStrategy,
  Component,
  effect,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideX } from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { ChatApi } from '../api/chat-api';
import { FileSlice } from '../api/models';
import { Highlighter, Token, languageForFile } from '../chat/highlight';
import { DocCitation } from './docs-models';

interface Line {
  number: number;
  tokens: Token[];
}

/** The code behind a `file:lines` reference of a page, read with the same endpoint as the chat. */
@Component({
  selector: 'app-code-panel',
  imports: [NgIcon, TranslocoDirective],
  providers: [provideIcons({ lucideX })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ng-container *transloco="let t">
      <section
        class="overflow-hidden rounded-md border border-border-2 bg-surface-2 shadow-md"
        [attr.aria-label]="citation().ref"
      >
        <div
          class="flex flex-wrap items-center gap-2 border-b border-border px-3 py-1.5 text-xs text-text-3"
        >
          <span class="font-mono text-foreground"
            >{{ citation().application }}/{{ citation().ref }}</span
          >
          @if (slice()?.source?.code_version; as version) {
            <span>{{ t('chat.codeVersion') }}</span>
            <span class="font-mono">{{ version.slice(0, 7) }}</span>
          }
          @if (slice()?.stale) {
            <span class="text-warning">{{ t('chat.fileChanged') }}</span>
          }
          <button
            type="button"
            class="ml-auto inline-flex size-8 items-center justify-center rounded-md hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            [attr.aria-label]="t('docs.closeCode')"
            (click)="closed.emit()"
          >
            <ng-icon name="lucideX" aria-hidden="true" />
          </button>
        </div>
        @if (error(); as message) {
          <p class="p-3 text-danger" role="alert">{{ message }}</p>
        } @else if (lines().length === 0) {
          <p class="p-3 text-text-3" role="status">{{ t('chat.loadingCode') }}</p>
        } @else {
          <div
            class="max-h-[40vh] overflow-auto p-3 font-mono text-xs leading-relaxed"
            tabindex="0"
            data-code
          >
            @for (line of lines(); track line.number) {
              <div class="whitespace-pre">
                <span class="mr-3 inline-block w-8 select-none text-right text-text-3">{{
                  line.number
                }}</span>
                @for (token of line.tokens; track $index) {
                  <span
                    [style.color]="token.color"
                    [class.italic]="token.italic"
                    [class.font-bold]="token.bold"
                    >{{ token.text }}</span
                  >
                }
              </div>
            }
          </div>
        }
      </section>
    </ng-container>
  `,
})
export class CodePanelComponent {
  private readonly api = inject(ChatApi);
  private readonly highlighter = inject(Highlighter);

  readonly citation = input.required<DocCitation>();
  readonly closed = output<void>();

  protected readonly slice = signal<FileSlice | null>(null);
  protected readonly lines = signal<Line[]>([]);
  protected readonly error = signal<string | null>(null);
  private token = 0;

  constructor() {
    effect(() => {
      const citation = this.citation();
      void this.load(citation);
    });
  }

  private async load(citation: DocCitation): Promise<void> {
    const mine = ++this.token;
    this.slice.set(null);
    this.lines.set([]);
    this.error.set(null);
    try {
      const slice = await this.api.fileSlice(
        citation.application,
        citation.file,
        citation.start_line,
        citation.end_line,
      );
      const highlighted = await this.highlighter.tokens(
        slice.text,
        languageForFile(slice.source.file),
      );
      if (mine !== this.token) return;
      this.slice.set(slice);
      this.lines.set(
        slice.text.split('\n').map((text, index) => ({
          number: slice.source.start_line + index,
          tokens: highlighted?.[index] ?? [{ text }],
        })),
      );
    } catch (error) {
      if (mine !== this.token) return;
      this.error.set(error instanceof Error ? error.message : 'The code could not be loaded.');
    }
  }
}
