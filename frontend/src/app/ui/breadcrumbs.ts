import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideChevronRight } from '@ng-icons/lucide';

export interface Crumb {
  label: string;
  /** Router commands; the last crumb (the current page) has none. */
  link?: string[];
  query?: Record<string, string>;
}

/** Where the current page is in the hierarchy (ui-spec §6.8). */
@Component({
  selector: 'app-breadcrumbs',
  imports: [RouterLink, NgIcon],
  providers: [provideIcons({ lucideChevronRight })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <nav [attr.aria-label]="label()">
      <ol class="flex flex-wrap items-center gap-x-1 gap-y-0.5 text-xs text-text-3">
        @for (crumb of items(); track $index; let last = $last) {
          <li class="flex items-center gap-1">
            @if (crumb.link && !last) {
              <a
                class="rounded-sm underline-offset-2 hover:text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                [routerLink]="crumb.link"
                [queryParams]="crumb.query"
                >{{ crumb.label }}</a
              >
            } @else {
              <span [attr.aria-current]="last ? 'page' : null" [class.text-text-2]="last">{{
                crumb.label
              }}</span>
            }
            @if (!last) {
              <ng-icon name="lucideChevronRight" class="size-3" aria-hidden="true" />
            }
          </li>
        }
      </ol>
    </nav>
  `,
})
export class BreadcrumbsComponent {
  readonly items = input.required<Crumb[]>();
  readonly label = input.required<string>();
}
