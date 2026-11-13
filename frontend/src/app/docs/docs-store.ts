import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiError } from '../api/chat-api';
import { DocsApi } from './docs-api';
import { AppDocs, DocApp, DocPage } from './docs-models';

export type LoadState = 'idle' | 'loading' | 'ready' | 'missing' | 'error';

function message(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

/** What the documentation viewer shows: the applications, one application's pages, one page. */
@Injectable({ providedIn: 'root' })
export class DocsStore {
  private readonly api = inject(DocsApi);

  readonly apps = signal<DocApp[]>([]);
  readonly appsState = signal<LoadState>('idle');
  readonly appsError = signal<string | null>(null);

  readonly docs = signal<AppDocs | null>(null);
  readonly docsState = signal<LoadState>('idle');
  readonly docsError = signal<string | null>(null);

  readonly page = signal<DocPage | null>(null);
  readonly pageState = signal<LoadState>('idle');
  readonly pageError = signal<string | null>(null);

  /** Applications that have documentation, so a reference to another one can be told apart. */
  readonly documented = computed(() =>
    this.apps()
      .filter((a) => a.has_docs)
      .map((a) => a.name),
  );

  private docsToken = 0;
  private pageToken = 0;
  private requested: { app: string; page: string | undefined } | null = null;

  async loadApps(): Promise<void> {
    this.appsState.set('loading');
    this.appsError.set(null);
    try {
      this.apps.set(await this.api.applications());
      this.appsState.set('ready');
    } catch (error) {
      this.appsError.set(message(error));
      this.appsState.set('error');
    }
  }

  /** Shows `path` of `app`'s documentation (its first page when `path` is missing or unknown). */
  async show(app: string | undefined, path: string | undefined): Promise<void> {
    this.requested = app ? { app, page: path } : null;
    if (!app) {
      this.docsToken++;
      this.pageToken++;
      this.docs.set(null);
      this.page.set(null);
      this.docsState.set('idle');
      this.pageState.set('idle');
      return;
    }
    if (this.docs()?.name !== app || this.docsState() !== 'ready') {
      await this.loadDocs(app);
    }
    const docs = this.docs();
    if (!docs || docs.name !== app || this.requested?.app !== app) return;
    const target = docs.pages.find((p) => p.path === path)?.path ?? docs.pages[0]?.path;
    if (!target) return;
    const shown = this.page();
    if (shown?.application === app && shown.path === target && this.pageState() === 'ready') {
      return;
    }
    await this.loadPage(app, target);
  }

  retry(): Promise<void> {
    const wanted = this.requested;
    this.docs.set(null);
    this.page.set(null);
    return wanted ? this.show(wanted.app, wanted.page) : Promise.resolve();
  }

  private async loadDocs(app: string): Promise<void> {
    const mine = ++this.docsToken;
    this.docs.set(null);
    this.page.set(null);
    this.pageState.set('idle');
    this.docsState.set('loading');
    this.docsError.set(null);
    try {
      const docs = await this.api.application(app);
      if (mine !== this.docsToken) return;
      this.docs.set(docs);
      this.docsState.set('ready');
    } catch (error) {
      if (mine !== this.docsToken) return;
      if (error instanceof ApiError && error.status === 404) {
        this.docsState.set('missing');
      } else {
        this.docsError.set(message(error));
        this.docsState.set('error');
      }
    }
  }

  private async loadPage(app: string, path: string): Promise<void> {
    const mine = ++this.pageToken;
    this.pageState.set('loading');
    this.pageError.set(null);
    try {
      const page = await this.api.page(app, path);
      if (mine !== this.pageToken) return;
      this.page.set(page);
      this.pageState.set('ready');
    } catch (error) {
      if (mine !== this.pageToken) return;
      this.pageError.set(message(error));
      this.pageState.set('error');
    }
  }
}
