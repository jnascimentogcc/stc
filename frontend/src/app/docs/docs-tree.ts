import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideChevronDown, lucideChevronRight } from '@ng-icons/lucide';
import { DocPageRef } from './docs-models';

export interface TreeGroup {
  name: string;
  pages: DocPageRef[];
}

/**
 * The page tree of one application's documentation (ui-spec §6.8): groups that expand and collapse,
 * the group of the current page open by default, the current page highlighted.
 */
@Component({
  selector: 'app-docs-tree',
  imports: [RouterLink, NgIcon],
  providers: [provideIcons({ lucideChevronDown, lucideChevronRight })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block' },
  template: `
    <ul class="space-y-1">
      @for (group of groups(); track group.name; let index = $index) {
        <li>
          <button
            type="button"
            class="flex min-h-9 w-full items-center gap-1 rounded-md px-2 text-left text-xs font-semibold uppercase tracking-wide text-text-3 hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            [attr.aria-expanded]="isOpen(group)"
            [attr.aria-controls]="'docs-group-' + index"
            (click)="toggle(group)"
          >
            <ng-icon
              [name]="isOpen(group) ? 'lucideChevronDown' : 'lucideChevronRight'"
              class="size-3.5"
              aria-hidden="true"
            />
            {{ group.name }}
          </button>
          @if (isOpen(group)) {
            <ul [id]="'docs-group-' + index">
              @for (item of group.pages; track item.path) {
                <li>
                  <a
                    class="flex min-h-9 items-center rounded-md px-3 text-sm hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                    [class.bg-accent-soft-2]="item.path === current()"
                    [class.text-primary-hover]="item.path === current()"
                    [class.font-medium]="item.path === current()"
                    [routerLink]="['/docs', app()]"
                    [queryParams]="{ page: item.path }"
                    [attr.aria-current]="item.path === current() ? 'page' : null"
                    >{{ item.title }}</a
                  >
                </li>
              }
            </ul>
          }
        </li>
      }
    </ul>
  `,
})
export class DocsTreeComponent {
  readonly app = input.required<string>();
  readonly groups = input.required<TreeGroup[]>();
  readonly current = input<string | undefined>(undefined);
  /** While the pages are filtered, every group that has a match is open. */
  readonly filtering = input(false);

  private readonly chosen = signal<Record<string, boolean>>({});
  private readonly currentGroup = computed(
    () => this.groups().find((g) => g.pages.some((p) => p.path === this.current()))?.name,
  );

  protected isOpen(group: TreeGroup): boolean {
    if (this.filtering()) return true;
    return this.chosen()[group.name] ?? group.name === this.currentGroup();
  }

  protected toggle(group: TreeGroup): void {
    const open = this.isOpen(group);
    this.chosen.update((state) => ({ ...state, [group.name]: !open }));
  }
}
