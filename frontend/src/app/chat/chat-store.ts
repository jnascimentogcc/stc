import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiError, ChatApi } from '../api/chat-api';
import { Application, ChatEvent, Conversation, Message, Profile } from '../api/models';

export interface ToolStep {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: 'running' | 'ok' | 'error';
  summary?: string;
}

/** The question being answered right now: tool progress and the text received so far. */
export interface Turn {
  question: string;
  steps: ToolStep[];
  text: string;
  state: 'running' | 'error';
  error?: string;
}

function describe(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Something went wrong. Try again.';
}

@Injectable({ providedIn: 'root' })
export class ChatStore {
  private readonly api = inject(ChatApi);
  private abort: AbortController | null = null;
  private nextLocalId = -1;

  readonly conversations = signal<Conversation[]>([]);
  readonly applications = signal<Application[]>([]);
  readonly current = signal<Conversation | null>(null);
  readonly messages = signal<Message[]>([]);
  readonly turn = signal<Turn | null>(null);
  readonly loadingList = signal(false);
  readonly loadingConversation = signal(false);
  /** A problem next to the region it affects (ui-spec §7): the list, or the conversation. */
  readonly listError = signal<string | null>(null);
  readonly conversationError = signal<string | null>(null);

  /** Scope and profile of a conversation that does not exist yet. */
  readonly draftScope = signal<string[] | null>(null);
  readonly draftProfile = signal<Profile>('technical');

  readonly busy = computed(() => this.turn()?.state === 'running');
  readonly scope = computed<string[] | null>(() => {
    const conversation = this.current();
    if (!conversation) return this.draftScope();
    return conversation.scope.type === 'applications' ? conversation.scope.applications : null;
  });
  readonly profile = computed<Profile>(() => this.current()?.profile ?? this.draftProfile());

  async loadList(): Promise<void> {
    this.loadingList.set(true);
    this.listError.set(null);
    try {
      const [conversations, applications] = await Promise.all([
        this.api.conversations(),
        this.api.applications(),
      ]);
      this.conversations.set(conversations);
      this.applications.set(applications);
    } catch (error) {
      this.listError.set(describe(error));
    } finally {
      this.loadingList.set(false);
    }
  }

  /** Opens a stored conversation, or the empty "new conversation" state when `id` is null. */
  async open(id: string | null): Promise<void> {
    if (id === this.current()?.id && this.conversationError() === null) return;
    this.stop();
    this.turn.set(null);
    this.conversationError.set(null);
    if (id === null) {
      this.current.set(null);
      this.messages.set([]);
      return;
    }
    this.loadingConversation.set(true);
    try {
      const { messages, ...conversation } = await this.api.conversation(id);
      this.current.set(conversation);
      this.messages.set(messages);
    } catch (error) {
      this.current.set(null);
      this.messages.set([]);
      this.conversationError.set(
        error instanceof ApiError && error.status === 404
          ? 'This conversation no longer exists.'
          : describe(error),
      );
    } finally {
      this.loadingConversation.set(false);
    }
  }

  async remove(id: string): Promise<boolean> {
    try {
      await this.api.deleteConversation(id);
    } catch (error) {
      this.listError.set(describe(error));
      return false;
    }
    this.conversations.update((list) => list.filter((c) => c.id !== id));
    if (this.current()?.id === id) {
      this.current.set(null);
      this.messages.set([]);
      this.turn.set(null);
    }
    return true;
  }

  async setScope(applications: string[] | null): Promise<void> {
    const conversation = this.current();
    if (!conversation) {
      this.draftScope.set(applications);
      return;
    }
    await this.change(
      conversation.id,
      applications === null ? { all_applications: true } : { applications },
    );
  }

  async setProfile(profile: Profile): Promise<void> {
    const conversation = this.current();
    if (!conversation) {
      this.draftProfile.set(profile);
      return;
    }
    await this.change(conversation.id, { profile });
  }

  private async change(
    id: string,
    changes: { applications?: string[]; all_applications?: boolean; profile?: Profile },
  ): Promise<void> {
    try {
      const updated = await this.api.updateConversation(id, changes);
      this.current.set(updated);
      this.replaceInList(updated);
      this.conversationError.set(null);
    } catch (error) {
      this.conversationError.set(describe(error));
    }
  }

  private replaceInList(conversation: Conversation): void {
    this.conversations.update((list) => [
      conversation,
      ...list.filter((c) => c.id !== conversation.id),
    ]);
  }

  /** Asks a question, creating the conversation first when there is none. */
  async send(question: string): Promise<void> {
    const text = question.trim();
    if (!text || this.busy()) return;
    this.conversationError.set(null);

    let conversation = this.current();
    if (!conversation) {
      try {
        conversation = await this.api.createConversation(this.draftScope(), this.draftProfile());
      } catch (error) {
        this.conversationError.set(describe(error));
        return;
      }
      this.current.set(conversation);
      this.replaceInList(conversation);
    }

    this.messages.update((list) => [
      ...list,
      {
        id: this.nextLocalId--,
        role: 'user',
        content: text,
        citations: [],
        created_at: new Date().toISOString(),
      },
    ]);
    this.turn.set({ question: text, steps: [], text: '', state: 'running' });
    this.abort = new AbortController();

    try {
      for await (const event of this.api.ask(conversation.id, text, this.abort.signal)) {
        this.apply(event);
      }
    } catch (error) {
      this.fail(describe(error));
    }
    if (this.turn()?.state === 'running') {
      // The stream ended without an answer: stopped by the user, or cut by the server.
      if (this.abort?.signal.aborted) this.turn.set(null);
      else this.fail('The answer was interrupted. Try again.');
    }
    this.abort = null;
    void this.refreshTitle(conversation.id);
  }

  private apply(event: ChatEvent): void {
    const turn = this.turn();
    if (!turn) return;
    switch (event.event) {
      case 'tool_call':
        this.turn.set({
          ...turn,
          steps: [
            ...turn.steps,
            {
              id: event.data.id,
              name: event.data.name,
              arguments: event.data.arguments,
              status: 'running',
            },
          ],
        });
        break;
      case 'tool_result':
        this.turn.set({
          ...turn,
          steps: turn.steps.map((step) =>
            step.id === event.data.id
              ? { ...step, status: event.data.ok ? 'ok' : 'error', summary: event.data.summary }
              : step,
          ),
        });
        break;
      case 'text':
        this.turn.set({ ...turn, text: turn.text + event.data.delta });
        break;
      case 'done':
        this.messages.update((list) => [
          ...list,
          {
            id: event.data.message_id,
            role: 'assistant',
            content: event.data.answer,
            citations: event.data.citations,
            created_at: new Date().toISOString(),
            warnings: event.data.warnings,
          },
        ]);
        this.turn.set(null);
        break;
      case 'error':
        this.fail(event.data.message);
        break;
    }
  }

  private fail(message: string): void {
    const turn = this.turn();
    if (turn) this.turn.set({ ...turn, state: 'error', error: message });
  }

  /** Stops waiting for the answer (the server may still finish it). */
  stop(): void {
    this.abort?.abort();
  }

  /** Retries the question of a failed turn. */
  async retry(): Promise<void> {
    const turn = this.turn();
    if (!turn || turn.state !== 'error') return;
    this.messages.update((list) => list.slice(0, -1)); // the question is sent again
    this.turn.set(null);
    await this.send(turn.question);
  }

  private async refreshTitle(id: string): Promise<void> {
    try {
      const { messages: _messages, ...fresh } = await this.api.conversation(id);
      if (this.current()?.id === id) this.current.set(fresh);
      this.replaceInList(fresh);
    } catch {
      // The title is cosmetic; the list refreshes on the next load.
    }
  }

  async flag(
    messageId: number,
    rating: 'incorrect' | 'incomplete',
    correction: string | null,
  ): Promise<string | null> {
    try {
      await this.api.feedback(messageId, rating, correction);
      return null;
    } catch (error) {
      return describe(error);
    }
  }
}
