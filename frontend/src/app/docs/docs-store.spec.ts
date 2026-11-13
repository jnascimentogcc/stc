import { TestBed } from '@angular/core/testing';
import { ApiError } from '../api/chat-api';
import { DocsApi } from './docs-api';
import { DocsStore } from './docs-store';
import { AppDocs, DocPage } from './docs-models';

const docs: AppDocs = {
  name: 'loans',
  status: 'complete',
  generated_at: 'now',
  code_version: null,
  outdated: false,
  model: 'm',
  language: 'en',
  pages: [
    { path: 'index.md', title: 'Overview', group: 'Start' },
    { path: 'data-model.md', title: 'Data', group: 'Reference' },
  ],
};
const page = (path: string): DocPage => ({
  application: 'loans',
  path,
  title: path,
  group: 'g',
  content: '# x',
});

function setup(application: () => Promise<AppDocs>) {
  const api = {
    applications: vi.fn(async () => [{ name: 'loans', has_docs: true }]),
    application: vi.fn(application),
    page: vi.fn(async (_a: string, path: string) => page(path)),
  };
  TestBed.configureTestingModule({ providers: [{ provide: DocsApi, useValue: api }] });
  return { api, store: TestBed.inject(DocsStore) };
}

describe('DocsStore', () => {
  it('lists the documented applications', async () => {
    const { store } = setup(async () => docs);
    await store.loadApps();
    expect(store.documented()).toEqual(['loans']);
  });

  it('shows the first page when none is asked for or the path is unknown', async () => {
    const { store } = setup(async () => docs);
    await store.show('loans', undefined);
    expect(store.page()?.path).toBe('index.md');
    await store.show('loans', 'nope.md');
    expect(store.page()?.path).toBe('index.md');
  });

  it('loads another page without reloading the application', async () => {
    const { store, api } = setup(async () => docs);
    await store.show('loans', 'index.md');
    await store.show('loans', 'data-model.md');
    expect(store.page()?.path).toBe('data-model.md');
    expect(api.application).toHaveBeenCalledTimes(1);
  });

  it('reports an application without documentation as missing', async () => {
    const { store } = setup(async () => {
      throw new ApiError(404, 'none');
    });
    await store.show('loans', undefined);
    expect(store.docsState()).toBe('missing');
  });

  it('reports other failures and can retry', async () => {
    let fail = true;
    const { store } = setup(async () => {
      if (fail) throw new ApiError(500, 'boom');
      return docs;
    });
    await store.show('loans', undefined);
    expect(store.docsState()).toBe('error');
    fail = false;
    await store.retry();
    expect(store.docsState()).toBe('ready');
  });
});
