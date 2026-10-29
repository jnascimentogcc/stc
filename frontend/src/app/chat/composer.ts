import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  inject,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideSend, lucideSquare } from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmTextareaImports } from '@spartan-ng/helm/textarea';
import { ChatStore } from './chat-store';

const MAX_LENGTH = 8000;

@Component({
  selector: 'app-composer',
  imports: [NgIcon, TranslocoDirective, ...HlmButtonImports, ...HlmTextareaImports],
  providers: [provideIcons({ lucideSend, lucideSquare })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ng-container *transloco="let t">
      <form class="flex items-end gap-2" (submit)="$event.preventDefault(); submit()">
        <div class="flex-1">
          <label class="sr-only" for="question">{{ t('chat.question') }}</label>
          <textarea
            #field
            hlmTextarea
            id="question"
            class="max-h-48 min-h-11 resize-none"
            rows="2"
            [attr.maxlength]="maxLength"
            [attr.placeholder]="t('chat.placeholder')"
            [attr.aria-describedby]="'question-hint'"
            [value]="text()"
            (input)="text.set($any($event.target).value)"
            (keydown)="onKey($event)"
          ></textarea>
          <p id="question-hint" class="mt-1 text-xs text-text-3">{{ t('chat.composerHint') }}</p>
        </div>
        @if (store.busy()) {
          <button
            hlmBtn
            variant="secondary"
            size="lg"
            class="mb-6"
            type="button"
            (click)="store.stop()"
          >
            <ng-icon name="lucideSquare" aria-hidden="true" />
            {{ t('chat.stop') }}
          </button>
        } @else {
          <button
            hlmBtn
            size="lg"
            class="mb-6"
            type="submit"
            [disabled]="text().trim().length === 0"
          >
            <ng-icon name="lucideSend" aria-hidden="true" />
            {{ t('chat.send') }}
          </button>
        }
      </form>
    </ng-container>
  `,
})
export class ComposerComponent {
  protected readonly store = inject(ChatStore);
  protected readonly maxLength = MAX_LENGTH;
  protected readonly text = signal('');
  private readonly field = viewChild.required<ElementRef<HTMLTextAreaElement>>('field');

  readonly sent = output<void>();

  protected onKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      this.submit();
    }
  }

  protected submit(): void {
    const question = this.text().trim();
    if (!question || this.store.busy()) return;
    this.text.set('');
    this.field().nativeElement.value = '';
    this.sent.emit();
    void this.store.send(question);
  }

  focus(): void {
    this.field().nativeElement.focus();
  }
}
