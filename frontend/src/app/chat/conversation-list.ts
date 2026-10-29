import { ChangeDetectionStrategy, Component, inject, output, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucidePlus, lucideTrash2 } from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { ChatStore } from './chat-store';

@Component({
  selector: 'app-conversation-list',
  imports: [NgIcon, RouterLink, TranslocoDirective, ...HlmButtonImports],
  providers: [provideIcons({ lucidePlus, lucideTrash2 })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ng-container *transloco="let t">
      <nav [attr.aria-label]="t('chat.conversations')" class="flex h-full flex-col gap-2">
        <a hlmBtn variant="secondary" routerLink="/" (click)="navigated.emit()">
          <ng-icon name="lucidePlus" aria-hidden="true" />
          {{ t('chat.newConversation') }}
        </a>

        @if (store.listError()) {
          <p class="rounded-md border border-danger-border bg-danger-bg p-2" role="alert">
            {{ store.listError() }}
            <button hlmBtn variant="link" size="sm" type="button" (click)="store.loadList()">
              {{ t('chat.retry') }}
            </button>
          </p>
        }

        @if (store.loadingList() && store.conversations().length === 0) {
          <p class="px-1 text-text-3" role="status">{{ t('chat.loading') }}</p>
        } @else if (store.conversations().length === 0 && !store.listError()) {
          <p class="px-1 text-text-3">{{ t('chat.noConversations') }}</p>
        }

        <ul class="flex-1 overflow-y-auto">
          @for (conversation of store.conversations(); track conversation.id) {
            <li class="group relative">
              <a
                class="block rounded-md py-2 pl-2.5 pr-10 hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-primary"
                [class.bg-accent-soft-2]="conversation.id === store.current()?.id"
                [routerLink]="['/c', conversation.id]"
                [attr.aria-current]="conversation.id === store.current()?.id ? 'page' : null"
                (click)="navigated.emit()"
              >
                <span class="line-clamp-2 break-words">{{
                  conversation.title ?? t('chat.untitled')
                }}</span>
                <span class="mt-0.5 block text-xs text-text-3">
                  {{
                    conversation.scope.type === 'root'
                      ? t('chat.allApplications')
                      : count(conversation)
                  }}
                </span>
              </a>
              @if (confirming() === conversation.id) {
                <div
                  class="absolute inset-y-0 right-1 flex items-center gap-1 rounded-md bg-surface px-1 shadow-sm"
                >
                  <button
                    hlmBtn
                    variant="destructive"
                    size="sm"
                    type="button"
                    (click)="remove(conversation.id)"
                  >
                    {{ t('chat.confirmDelete') }}
                  </button>
                  <button
                    hlmBtn
                    variant="ghost"
                    size="sm"
                    type="button"
                    (click)="confirming.set(null)"
                  >
                    {{ t('chat.cancel') }}
                  </button>
                </div>
              } @else {
                <button
                  hlmBtn
                  variant="ghost"
                  size="icon-sm"
                  class="absolute right-1 top-1.5"
                  type="button"
                  [attr.aria-label]="t('chat.deleteConversation')"
                  (click)="confirming.set(conversation.id)"
                >
                  <ng-icon name="lucideTrash2" aria-hidden="true" />
                </button>
              }
            </li>
          }
        </ul>
      </nav>
    </ng-container>
  `,
})
export class ConversationListComponent {
  protected readonly store = inject(ChatStore);
  private readonly router = inject(Router);
  protected readonly confirming = signal<string | null>(null);

  /** Lets the page close the mobile drawer after a choice. */
  readonly navigated = output<void>();

  protected count(conversation: { scope: { type: string; applications?: string[] } }): string {
    return (conversation.scope.applications ?? []).join(', ');
  }

  protected async remove(id: string): Promise<void> {
    this.confirming.set(null);
    const wasOpen = this.store.current()?.id === id;
    const removed = await this.store.remove(id);
    if (removed && wasOpen) await this.router.navigateByUrl('/');
  }
}
