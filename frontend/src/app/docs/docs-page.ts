import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideChevronLeft,
  lucideChevronRight,
  lucideDownload,
  lucideTriangleAlert,
} from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { ThemeService } from '../theme';
import { CodePanelComponent } from './code-panel';
import { DocContentComponent, DocLink } from './doc-content';
import { DocsApi } from './docs-api';
import { renderDoc } from './docs-markdown';
import { DocCitation, DocPageRef } from './docs-models';
import { DocsStore } from './docs-store';

interface Group {
  name: string;
  pages: DocPageRef[];
}

@Component({
  selector: 'app-docs-page',
  imports: [
    CodePanelComponent,
    DocContentComponent,
    NgIcon,
    NgTemplateOutlet,
    RouterLink,
    TranslocoDirective,
    ...HlmButtonImports,
  ],
  providers: [
    provideIcons({ lucideChevronLeft, lucideChevronRight, lucideDownload, lucideTriangleAlert }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './docs-page.html',
  host: { class: 'block' },
})
export class DocsPage {
  protected readonly store = inject(DocsStore);
  protected readonly theme = inject(ThemeService);
  private readonly api = inject(DocsApi);
  private readonly router = inject(Router);

  /** The application in the URL (`/docs/:app`); none shows the list of applications. */
  readonly app = input<string | undefined>(undefined);
  /** The page in the query string (`?page=features/job-x.md`); the first page by default. */
  readonly page = input<string | undefined>(undefined);

  protected readonly filter = signal('');
  protected readonly openCode = signal<DocCitation | null>(null);

  protected readonly rendered = computed(() => {
    const page = this.store.page();
    if (!page) return null;
    return renderDoc(page.content, {
      application: page.application,
      path: page.path,
      applications: this.store.documented(),
    });
  });

  protected readonly groups = computed<Group[]>(() => {
    const needle = this.filter().trim().toLowerCase();
    const groups: Group[] = [];
    for (const page of this.store.docs()?.pages ?? []) {
      if (needle && !page.title.toLowerCase().includes(needle)) continue;
      const last = groups.at(-1);
      if (last?.name === page.group) last.pages.push(page);
      else groups.push({ name: page.group, pages: [page] });
    }
    return groups;
  });

  protected readonly neighbours = computed(() => {
    const pages = this.store.docs()?.pages ?? [];
    const index = pages.findIndex((p) => p.path === this.store.page()?.path);
    return {
      previous: index > 0 ? pages[index - 1] : null,
      next: index >= 0 && index < pages.length - 1 ? pages[index + 1] : null,
    };
  });

  protected readonly toc = computed(() =>
    (this.rendered()?.headings ?? []).filter((h) => h.level === 2),
  );

  protected readonly exportUrl = computed(() => {
    const app = this.app();
    return app ? this.api.exportUrl(app) : '';
  });

  constructor() {
    if (this.store.appsState() === 'idle') void this.store.loadApps();
    effect(() => {
      const app = this.app();
      const page = this.page();
      untracked(() => void this.store.show(app, page));
    });
    effect(() => {
      this.store.page();
      untracked(() => {
        this.openCode.set(null);
        globalThis.scrollTo?.({ top: 0 });
      });
    });
  }

  protected chooseApp(name: string): void {
    void this.router.navigate(name ? ['/docs', name] : ['/docs']);
  }

  protected open(link: DocLink): void {
    const app = this.app();
    if (app) void this.router.navigate(['/docs', app], { queryParams: { page: link.path } });
  }

  protected link(path: string): { page: string } {
    return { page: path };
  }

  protected scrollTo(id: string): void {
    const target = document.getElementById(id);
    target?.scrollIntoView?.({ block: 'start' });
    target?.focus?.({ preventScroll: true });
  }
}
