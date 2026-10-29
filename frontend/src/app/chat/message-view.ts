import {
  ChangeDetectionStrategy,
  Component,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideTriangleAlert } from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { HlmAlertImports } from '@spartan-ng/helm/alert';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmTextareaImports } from '@spartan-ng/helm/textarea';
import { Message } from '../api/models';
import { ChatStore } from './chat-store';
import { CitationListComponent } from './citation-list';
import { MarkdownComponent } from './markdown';

type Rating = 'incorrect' | 'incomplete';

@Component({
  selector: 'app-message-view',
  imports: [
    CitationListComponent,
    MarkdownComponent,
    NgIcon,
    TranslocoDirective,
    ...HlmAlertImports,
    ...HlmButtonImports,
    ...HlmTextareaImports,
  ],
  providers: [provideIcons({ lucideTriangleAlert })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ng-container *transloco="let t">
      @if (message().role === 'user') {
        <div class="flex justify-end">
          <p
            class="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-accent-soft-2 px-3.5 py-2"
          >
            <span class="sr-only">{{ t('chat.you') }}: </span>{{ message().content }}
          </p>
        </div>
      } @else {
        <article
          class="rounded-lg border border-border bg-surface px-4 py-3 shadow-sm"
          [attr.aria-label]="t('chat.answer')"
        >
          <app-markdown
            [source]="message().content"
            [citations]="message().citations"
            [highlight]="true"
            (referenceOpened)="sources()?.open($event)"
          />

          @for (warning of message().warnings ?? []; track warning) {
            <div hlmAlert class="mt-3" role="status">
              <ng-icon name="lucideTriangleAlert" aria-hidden="true" />
              <p hlmAlertDescription>{{ warning }}</p>
            </div>
          }
          @if (unverifiedCount() > 0 && !(message().warnings ?? []).length) {
            <div hlmAlert class="mt-3" role="status">
              <ng-icon name="lucideTriangleAlert" aria-hidden="true" />
              <p hlmAlertDescription>{{ t('chat.someUnverified') }}</p>
            </div>
          }

          <app-citation-list [citations]="message().citations" />

          <div class="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-2">
            @if (sent()) {
              <p class="text-xs text-text-2" role="status">
                {{ proposed() ? t('chat.feedbackAndProposalSaved') : t('chat.feedbackSaved') }}
              </p>
              @if (error()) {
                <p class="text-danger" role="alert">{{ error() }}</p>
              }
            } @else if (rating() === null) {
              <span class="text-xs text-text-3">{{ t('chat.feedbackPrompt') }}</span>
              <button
                hlmBtn
                variant="ghost"
                size="sm"
                type="button"
                (click)="rating.set('incorrect')"
              >
                {{ t('chat.incorrect') }}
              </button>
              <button
                hlmBtn
                variant="ghost"
                size="sm"
                type="button"
                (click)="rating.set('incomplete')"
              >
                {{ t('chat.incomplete') }}
              </button>
            } @else {
              <form class="flex w-full flex-col gap-2" (submit)="$event.preventDefault(); submit()">
                <label class="text-xs font-medium" [for]="'fb-' + message().id">
                  {{ rating() === 'incorrect' ? t('chat.whatIsWrong') : t('chat.whatIsMissing') }}
                  <span class="font-normal text-text-3">({{ t('chat.optional') }})</span>
                </label>
                <textarea
                  hlmTextarea
                  [id]="'fb-' + message().id"
                  rows="3"
                  [value]="correction()"
                  (input)="correction.set($any($event.target).value)"
                ></textarea>
                @if (correction().trim() && store.applications().length > 0) {
                  <fieldset class="flex flex-col gap-2 rounded-md border border-border p-3">
                    <legend class="px-1 text-xs font-medium">{{ t('chat.proposeTitle') }}</legend>
                    <label class="inline-flex min-h-8 items-center gap-2">
                      <input
                        type="checkbox"
                        class="accent-primary"
                        [checked]="propose()"
                        (change)="togglePropose($any($event.target).checked)"
                      />
                      {{ t('chat.proposeToggle') }}
                    </label>
                    @if (propose()) {
                      <p class="text-xs text-text-3">{{ t('chat.proposeHelp') }}</p>
                      <div class="flex flex-wrap gap-3">
                        <label class="flex flex-col gap-1 text-xs font-medium">
                          {{ t('chat.proposeApplication') }}
                          <select
                            class="h-9 rounded-md border border-input bg-background px-2 text-sm font-normal"
                            [value]="application()"
                            (change)="application.set($any($event.target).value)"
                          >
                            @for (item of store.applications(); track item.name) {
                              <option [value]="item.name" [selected]="item.name === application()">
                                {{ item.name }}
                              </option>
                            }
                          </select>
                        </label>
                        <label class="flex flex-col gap-1 text-xs font-medium">
                          {{ t('chat.proposeSection') }}
                          <select
                            class="h-9 rounded-md border border-input bg-background px-2 text-sm font-normal"
                            [value]="section()"
                            (change)="section.set($any($event.target).value)"
                          >
                            @for (item of store.sections(); track item.id) {
                              <option [value]="item.id" [selected]="item.id === section()">
                                {{ item.title }}
                              </option>
                            }
                          </select>
                        </label>
                      </div>
                    }
                  </fieldset>
                }
                @if (error()) {
                  <p class="text-danger" role="alert">{{ error() }}</p>
                }
                <div class="flex gap-2">
                  <button hlmBtn size="sm" type="submit" [disabled]="saving()">
                    {{ t('chat.sendFeedback') }}
                  </button>
                  <button hlmBtn variant="ghost" size="sm" type="button" (click)="cancel()">
                    {{ t('chat.cancel') }}
                  </button>
                </div>
              </form>
            }
          </div>
        </article>
      }
    </ng-container>
  `,
})
export class MessageViewComponent {
  private readonly store = inject(ChatStore);

  readonly message = input.required<Message>();
  protected readonly sources = viewChild(CitationListComponent);

  protected readonly rating = signal<Rating | null>(null);
  protected readonly correction = signal('');
  protected readonly saving = signal(false);
  protected readonly sent = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly propose = signal(false);
  protected readonly proposed = signal(false);
  protected readonly application = signal('');
  protected readonly section = signal('');

  protected unverifiedCount(): number {
    return this.message().citations.filter((c) => !c.verified).length;
  }

  protected cancel(): void {
    this.rating.set(null);
    this.propose.set(false);
    this.error.set(null);
  }

  /** Offers the application the answer cited first (else the first one) and the first section. */
  protected togglePropose(on: boolean): void {
    this.propose.set(on);
    if (!on) return;
    void this.store.loadSections().then(() => {
      if (!this.section()) this.section.set(this.store.sections()[0]?.id ?? '');
    });
    if (!this.application()) {
      const cited = this.message().citations.find((c) => c.application)?.application;
      this.application.set(cited ?? this.store.applications()[0]?.name ?? '');
    }
  }

  protected async submit(): Promise<void> {
    const rating = this.rating();
    if (!rating || this.saving()) return;
    this.saving.set(true);
    this.error.set(null);
    const text = this.correction().trim() || null;
    const saved = await this.store.flag(this.message().id, rating, text);
    if ('error' in saved) {
      this.saving.set(false);
      this.error.set(saved.error);
      return;
    }
    // The feedback is kept even if the proposal fails: say so, and what to do.
    if (this.propose() && text && this.application() && this.section()) {
      const problem = await this.store.propose(saved.id, this.application(), this.section(), text);
      if (problem) this.error.set(`${problem}`);
      else this.proposed.set(true);
    }
    this.saving.set(false);
    this.sent.set(true);
  }
}
