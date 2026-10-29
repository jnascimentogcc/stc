import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideCircleAlert, lucideCircleCheck, lucideLoaderCircle } from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { ChatStore, ToolStep, Turn } from './chat-store';
import { MarkdownComponent } from './markdown';

/** The answer being produced: which tools the agent is using, then the text as it arrives. */
@Component({
  selector: 'app-live-turn',
  imports: [MarkdownComponent, NgIcon, TranslocoDirective, ...HlmButtonImports],
  providers: [provideIcons({ lucideCircleAlert, lucideCircleCheck, lucideLoaderCircle })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ng-container *transloco="let t">
      <section
        class="rounded-lg border border-border bg-surface px-4 py-3 shadow-sm"
        [attr.aria-label]="t('chat.answer')"
        [attr.aria-busy]="turn().state === 'running'"
      >
        @if (turn().steps.length > 0) {
          <details
            class="mb-2 text-xs text-text-2"
            [open]="turn().state === 'running' && !turn().text"
          >
            <summary class="cursor-pointer select-none">
              {{ t('chat.steps', { count: turn().steps.length }) }}
            </summary>
            <ol class="mt-1.5 flex flex-col gap-1">
              @for (step of turn().steps; track step.id) {
                <li class="flex items-start gap-1.5">
                  @switch (step.status) {
                    @case ('running') {
                      <ng-icon
                        name="lucideLoaderCircle"
                        class="mt-0.5 animate-spin"
                        aria-hidden="true"
                      />
                    }
                    @case ('ok') {
                      <ng-icon
                        name="lucideCircleCheck"
                        class="mt-0.5 text-success"
                        aria-hidden="true"
                      />
                    }
                    @default {
                      <ng-icon
                        name="lucideCircleAlert"
                        class="mt-0.5 text-warning"
                        aria-hidden="true"
                      />
                    }
                  }
                  <span class="font-mono">{{ label(step) }}</span>
                  @if (step.summary) {
                    <span class="text-text-3">— {{ step.summary }}</span>
                  }
                </li>
              }
            </ol>
          </details>
        }

        @if (turn().text) {
          <app-markdown [source]="turn().text" />
        } @else if (turn().state === 'running') {
          <p class="text-text-3">{{ t('chat.working') }}</p>
        }

        @if (turn().state === 'error') {
          <div class="mt-2 rounded-md border border-danger-border bg-danger-bg p-3" role="alert">
            <p class="font-medium">{{ t('chat.failed') }}</p>
            <p class="mt-0.5 break-words">{{ turn().error }}</p>
            <button hlmBtn size="sm" class="mt-2" type="button" (click)="store.retry()">
              {{ t('chat.retry') }}
            </button>
          </div>
        }
      </section>
    </ng-container>
  `,
})
export class LiveTurnComponent {
  protected readonly store = inject(ChatStore);

  readonly turn = input.required<Turn>();

  protected label(step: ToolStep): string {
    const args = Object.entries(step.arguments)
      .filter(([, value]) => typeof value === 'string' || typeof value === 'number')
      .map(([key, value]) => `${key}=${String(value)}`)
      .join(' ');
    return args ? `${step.name} ${args}` : step.name;
  }
}
