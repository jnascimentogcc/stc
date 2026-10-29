import { Injectable } from '@angular/core';
import { readSse } from './sse';
import {
  Application,
  ChatEvent,
  Conversation,
  Feedback,
  FileSlice,
  Message,
  Profile,
} from './models';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export interface ConversationWithMessages extends Conversation {
  messages: Message[];
}

const BASE = '/api';

async function failure(response: Response): Promise<ApiError> {
  let detail = response.statusText;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') detail = body.detail;
    else if (Array.isArray(body.detail)) detail = 'The request was not valid.';
  } catch {
    // Not JSON: keep the status text.
  }
  return new ApiError(response.status, detail);
}

@Injectable({ providedIn: 'root' })
export class ChatApi {
  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    let response: Response;
    try {
      response = await fetch(BASE + path, {
        method,
        headers: body === undefined ? undefined : { 'content-type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError(0, 'The server could not be reached.');
    }
    if (!response.ok) throw await failure(response);
    return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
  }

  applications(): Promise<Application[]> {
    return this.request('GET', '/applications');
  }

  conversations(): Promise<Conversation[]> {
    return this.request('GET', '/conversations');
  }

  conversation(id: string): Promise<ConversationWithMessages> {
    return this.request('GET', `/conversations/${encodeURIComponent(id)}`);
  }

  createConversation(applications: string[] | null, profile: Profile): Promise<Conversation> {
    return this.request('POST', '/conversations', { applications, profile });
  }

  updateConversation(
    id: string,
    changes: {
      title?: string;
      applications?: string[];
      all_applications?: boolean;
      profile?: Profile;
    },
  ): Promise<Conversation> {
    return this.request('PATCH', `/conversations/${encodeURIComponent(id)}`, changes);
  }

  deleteConversation(id: string): Promise<void> {
    return this.request('DELETE', `/conversations/${encodeURIComponent(id)}`);
  }

  feedback(
    messageId: number,
    rating: 'incorrect' | 'incomplete',
    correction: string | null,
  ): Promise<Feedback> {
    return this.request('POST', `/messages/${messageId}/feedback`, { rating, correction });
  }

  fileSlice(
    application: string,
    path: string,
    startLine: number,
    endLine: number,
  ): Promise<FileSlice> {
    const query = new URLSearchParams({
      application,
      path,
      start_line: String(startLine),
      end_line: String(endLine),
    });
    return this.request('GET', `/files/slice?${query}`);
  }

  /** Asks a question and yields the streamed events; aborting `signal` stops the stream. */
  async *ask(
    conversationId: string,
    content: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatEvent> {
    let response: Response;
    try {
      response = await fetch(
        `${BASE}/conversations/${encodeURIComponent(conversationId)}/messages`,
        {
          method: 'POST',
          headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
          body: JSON.stringify({ content }),
          signal,
        },
      );
    } catch (error) {
      if (signal?.aborted) return;
      throw new ApiError(0, 'The server could not be reached.');
    }
    if (!response.ok) throw await failure(response);
    if (!response.body) throw new ApiError(0, 'The server sent no answer.');
    try {
      for await (const message of readSse(response.body)) {
        yield message as ChatEvent;
      }
    } catch (error) {
      if (signal?.aborted) return;
      throw error instanceof ApiError ? error : new ApiError(0, 'The connection was interrupted.');
    }
  }
}
