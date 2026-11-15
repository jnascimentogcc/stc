import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideChevronLeft, lucideChevronRight } from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { DocPageRef } from './docs-models';

/** Previous and next page of the documentation, at the end of an article (ui-spec §6.8). */
@Component({
  selector: 'app-page-nav',
  imports: [RouterLink, NgIcon, TranslocoDirective],
  providers: [provideIcons({ lucideChevronLeft, lucideChevronRight })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block' },
  template: `
    <ng-container *transloco="let t">
      <nav
        class="mt-12 grid gap-3 border-t border-border pt-6 sm:grid-cols-2"
        [attr.aria-label]="t('docs.pageNavigation')"
      >
        @if (previous(); as page) {
          <a
            class="flex min-h-11 flex-col rounded-lg border border-border bg-surface p-4 hover:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            [routerLink]="['/docs', app()]"
            [queryParams]="{ page: page.path }"
          >
            <span class="flex items-center gap-1 text-xs text-text-3">
              <ng-icon name="lucideChevronLeft" aria-hidden="true" />{{ t('docs.previous') }}
            </span>
            <span class="font-medium text-primary">{{ page.title }}</span>
          </a>
        } @else {
          <span></span>
        }
        @if (next(); as page) {
          <a
            class="flex min-h-11 flex-col items-end rounded-lg border border-border bg-surface p-4 text-right hover:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            [routerLink]="['/docs', app()]"
            [queryParams]="{ page: page.path }"
          >
            <span class="flex items-center gap-1 text-xs text-text-3">
              {{ t('docs.next') }}<ng-icon name="lucideChevronRight" aria-hidden="true" />
            </span>
            <span class="font-medium text-primary">{{ page.title }}</span>
          </a>
        }
      </nav>
    </ng-container>
  `,
})
export class PageNavComponent {
  readonly app = input.required<string>();
  readonly previous = input<DocPageRef | null>(null);
  readonly next = input<DocPageRef | null>(null);
}
