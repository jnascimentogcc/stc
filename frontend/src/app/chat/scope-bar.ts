import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { TranslocoDirective } from '@jsverse/transloco';
import { Profile } from '../api/models';
import { ChatStore } from './chat-store';

/** What the conversation covers (CHT-06) and how the answers are written (CHT-07). */
@Component({
  selector: 'app-scope-bar',
  imports: [TranslocoDirective],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ng-container *transloco="let t">
      <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:gap-6">
        <fieldset class="min-w-0 flex-1" [disabled]="store.busy()">
          <legend class="mb-1 text-xs font-medium text-text-2">{{ t('chat.scope') }}</legend>
          <div class="flex flex-wrap gap-x-4 gap-y-1.5">
            <label class="inline-flex min-h-8 items-center gap-1.5">
              <input
                type="radio"
                name="scope"
                class="accent-primary"
                [checked]="store.scope() === null"
                (change)="store.setScope(null)"
              />
              {{ t('chat.allApplications') }}
            </label>
            <label class="inline-flex min-h-8 items-center gap-1.5">
              <input
                type="radio"
                name="scope"
                class="accent-primary"
                [checked]="store.scope() !== null"
                [disabled]="store.applications().length === 0"
                (change)="restrict()"
              />
              {{ t('chat.selectedApplications') }}
            </label>
          </div>
          @if (store.scope() !== null) {
            <ul class="mt-1.5 flex flex-wrap gap-x-4 gap-y-1" [attr.aria-label]="t('chat.scope')">
              @for (application of store.applications(); track application.name) {
                <li>
                  <label class="inline-flex min-h-8 items-center gap-1.5 font-mono text-xs">
                    <input
                      type="checkbox"
                      class="accent-primary"
                      [checked]="selected().has(application.name)"
                      (change)="toggle(application.name)"
                    />
                    {{ application.name }}
                  </label>
                </li>
              }
            </ul>
          }
        </fieldset>

        <fieldset [disabled]="store.busy()">
          <legend class="mb-1 text-xs font-medium text-text-2">{{ t('chat.profile') }}</legend>
          <div class="flex flex-wrap gap-x-4">
            @for (option of profiles; track option) {
              <label class="inline-flex min-h-8 items-center gap-1.5">
                <input
                  type="radio"
                  name="profile"
                  class="accent-primary"
                  [checked]="store.profile() === option"
                  (change)="store.setProfile(option)"
                />
                {{ t('chat.profile_' + option) }}
              </label>
            }
          </div>
        </fieldset>
      </div>
    </ng-container>
  `,
})
export class ScopeBarComponent {
  protected readonly store = inject(ChatStore);
  protected readonly profiles: Profile[] = ['technical', 'functional'];
  protected readonly selected = computed(() => new Set(this.store.scope() ?? []));

  /** "Selected applications" starts with the first one, since a scope cannot be empty. */
  protected restrict(): void {
    const first = this.store.applications()[0]?.name;
    if (first) void this.store.setScope([first]);
  }

  protected toggle(name: string): void {
    const next = new Set(this.selected());
    if (next.has(name)) next.delete(name);
    else next.add(name);
    // The last application stays: an empty scope would mean "nothing", which is not a scope.
    if (next.size > 0) void this.store.setScope([...next]);
  }
}
