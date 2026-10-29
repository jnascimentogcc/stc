import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { Router } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideMenu } from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { ChatStore } from './chat-store';
import { ComposerComponent } from './composer';
import { ConversationListComponent } from './conversation-list';
import { LiveTurnComponent } from './live-turn';
import { MessageViewComponent } from './message-view';
import { ScopeBarComponent } from './scope-bar';

@Component({
  selector: 'app-chat-page',
  imports: [
    ComposerComponent,
    ConversationListComponent,
    LiveTurnComponent,
    MessageViewComponent,
    NgIcon,
    ScopeBarComponent,
    TranslocoDirective,
    ...HlmButtonImports,
  ],
  providers: [provideIcons({ lucideMenu })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chat-page.html',
  host: { class: 'block' },
})
export class ChatPage {
  protected readonly store = inject(ChatStore);
  private readonly router = inject(Router);

  /** The conversation in the URL (`/c/:id`); none for a new conversation. */
  readonly id = input<string | undefined>(undefined);

  protected readonly drawerOpen = signal(false);
  private readonly scroller = viewChild.required<ElementRef<HTMLElement>>('scroller');
  private readonly composer = viewChild.required(ComposerComponent);
  private stickToBottom = true;

  protected readonly announcement = computed(() => {
    const turn = this.store.turn();
    if (turn?.state === 'error') return turn.error ?? '';
    if (turn) {
      const step = turn.steps.at(-1);
      return step ? step.name : '';
    }
    return '';
  });

  constructor() {
    void this.store.loadList();

    effect(() => {
      const id = this.id() ?? null;
      untracked(() => void this.store.open(id));
    });

    // A conversation created by the first question gets its own address.
    effect(() => {
      const current = this.store.current();
      if (current && this.id() === undefined) {
        untracked(() => void this.router.navigate(['/c', current.id], { replaceUrl: true }));
      }
    });

    // Keep the newest text in view, unless the reader scrolled up to read.
    effect(() => {
      this.store.messages();
      this.store.turn();
      untracked(() => {
        if (!this.stickToBottom) return;
        queueMicrotask(() => {
          const element = this.scroller().nativeElement;
          element.scrollTop = element.scrollHeight;
        });
      });
    });
  }

  protected onScroll(): void {
    const element = this.scroller().nativeElement;
    this.stickToBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
  }

  protected afterSend(): void {
    this.stickToBottom = true;
  }

  protected closeDrawer(): void {
    this.drawerOpen.set(false);
  }
}
