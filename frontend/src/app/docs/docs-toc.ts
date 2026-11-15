import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { DocHeading } from './docs-models';

const OFFSET = 88; // the sticky header plus a little air

/** "On this page" (ui-spec §6.8): the headings of the page, the one being read marked. */
@Component({
  selector: 'app-docs-toc',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block' },
  template: `
    <ul class="space-y-0.5 text-[13px]">
      @for (heading of headings(); track heading.id) {
        <li [class.pl-3]="heading.level > 2">
          <a
            class="block rounded-md px-2 py-1 hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            [class.text-text-2]="heading.id !== active()"
            [class.text-primary]="heading.id === active()"
            [class.font-medium]="heading.id === active()"
            [attr.aria-current]="heading.id === active() ? 'location' : null"
            [href]="'#' + heading.id"
            (click)="$event.preventDefault(); go(heading.id)"
            >{{ heading.text }}</a
          >
        </li>
      }
    </ul>
  `,
})
export class DocsTocComponent {
  readonly headings = input.required<DocHeading[]>();
  protected readonly active = signal<string | null>(null);

  constructor() {
    const destroy = inject(DestroyRef);
    let frame = 0;
    const update = (): void => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        let current: string | null = null;
        for (const heading of this.headings()) {
          const element = document.getElementById(heading.id);
          if (element && element.getBoundingClientRect().top <= OFFSET) current = heading.id;
        }
        this.active.set(current ?? this.headings()[0]?.id ?? null);
      });
    };
    globalThis.addEventListener?.('scroll', update, { passive: true });
    globalThis.addEventListener?.('resize', update, { passive: true });
    destroy.onDestroy(() => {
      cancelAnimationFrame(frame);
      globalThis.removeEventListener?.('scroll', update);
      globalThis.removeEventListener?.('resize', update);
    });
    effect(() => {
      this.headings();
      untracked(update);
    });
  }

  protected go(id: string): void {
    const target = document.getElementById(id);
    target?.scrollIntoView?.({ block: 'start' });
    target?.focus?.({ preventScroll: true });
    this.active.set(id);
  }
}
