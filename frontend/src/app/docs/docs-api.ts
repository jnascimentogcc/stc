import { Injectable } from '@angular/core';
import { ApiError, failure } from '../api/chat-api';
import { AppDocs, DocApp, DocPage } from './docs-models';

const BASE = '/api/docs';

/** Read-only access to the generated documentation (DOC-13). */
@Injectable({ providedIn: 'root' })
export class DocsApi {
  private async get<T>(path: string, params: Record<string, string> = {}): Promise<T> {
    const query = new URLSearchParams(params);
    let response: Response;
    try {
      response = await fetch(`${BASE}${path}${query.size ? `?${query}` : ''}`);
    } catch {
      throw new ApiError(0, 'The server could not be reached.');
    }
    if (!response.ok) throw await failure(response);
    return (await response.json()) as T;
  }

  applications(): Promise<DocApp[]> {
    return this.get('');
  }

  application(name: string): Promise<AppDocs> {
    return this.get('/app', { application: name });
  }

  page(application: string, path: string): Promise<DocPage> {
    return this.get('/page', { application, path });
  }

  /** Where the whole documentation of an application can be downloaded as one markdown file. */
  exportUrl(application: string): string {
    return `${BASE}/export?${new URLSearchParams({ application })}`;
  }
}
