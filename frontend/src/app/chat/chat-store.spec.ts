import { TestBed } from '@angular/core/testing';
import { ApiError, ChatApi } from '../api/chat-api';
import { ChatEvent, Conversation } from '../api/models';
import { ChatStore } from './chat-store';

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 'c1',
    title: null,
    scope: { type: 'root' },
    profile: 'technical',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

const DONE: ChatEvent = {
  event: 'done',
  data: {
    answer: 'It does X `a/b.py:1-2`.',
    citations: [
      {
        ref: 'a/b.py:1-2',
        verified: true,
        application: 'a',
        file: 'b.py',
        start_line: 1,
        end_line: 2,
      },
    ],
    warnings: ['careful'],
    steps: 2,
    usage: { input_tokens: 1, output_tokens: 1 },
    message_id: 7,
  },
};

class FakeApi {
  created: Array<[string[] | null, string]> = [];
  updates: unknown[] = [];
  deleted: string[] = [];
  events: ChatEvent[] = [];
  failWith: Error | null = null;
  asked: string[] = [];
  next = conversation();
  stored = conversation();

  applications = async () => [{ name: 'a', has_catalog: false }];
  conversations = async () => [];
  conversation = async (id: string) => ({ ...this.stored, id, messages: [] });
  createConversation = async (apps: string[] | null, profile: string) => {
    this.created.push([apps, profile]);
    return this.next;
  };
  updateConversation = async (_id: string, changes: Record<string, unknown>) => {
    this.updates.push(changes);
    return conversation({
      scope: changes['applications']
        ? { type: 'applications', applications: changes['applications'] as string[] }
        : { type: 'root' },
    });
  };
  deleteConversation = async (id: string) => {
    this.deleted.push(id);
  };
  feedback = async () => ({ id: 1, message_id: 7, rating: 'incorrect', correction: null });
  proposals: unknown[] = [];
  sectionCalls = 0;
  catalogSections = async () => {
    this.sectionCalls++;
    return [
      { id: 'purpose', title: 'Functional purpose' },
      { id: 'stack', title: 'Stack and versions' },
    ];
  };
  proposeCorrection = async (id: number, app: string, section: string, text: string | null) => {
    this.proposals.push([id, app, section, text]);
    return {
      id: 1,
      feedback_id: id,
      application: app,
      section,
      text: text ?? '',
      status: 'proposed',
    };
  };

  async *ask(_id: string, content: string, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
    this.asked.push(content);
    if (this.failWith) throw this.failWith;
    for (const event of this.events) {
      if (signal?.aborted) return;
      yield event;
    }
  }
}

describe('ChatStore', () => {
  let api: FakeApi;
  let store: ChatStore;

  beforeEach(() => {
    api = new FakeApi();
    TestBed.configureTestingModule({ providers: [{ provide: ChatApi, useValue: api }] });
    store = TestBed.inject(ChatStore);
  });

  it('creates the conversation with the draft scope on the first question and streams the answer', async () => {
    store.draftScope.set(['a']);
    store.draftProfile.set('functional');
    api.events = [
      { event: 'tool_call', data: { id: 't1', name: 'grep_code', arguments: { pattern: 'x' } } },
      {
        event: 'tool_result',
        data: { id: 't1', name: 'grep_code', ok: true, summary: '1 result(s)' },
      },
      { event: 'text', data: { delta: 'It does ' } },
      DONE,
    ];

    await store.send('  What does it do?  ');

    expect(api.created).toEqual([[['a'], 'functional']]);
    expect(api.asked).toEqual(['What does it do?']);
    expect(store.current()?.id).toBe('c1');
    expect(store.turn()).toBeNull();
    const [question, answer] = store.messages();
    expect([question.role, question.content]).toEqual(['user', 'What does it do?']);
    expect(answer).toMatchObject({ id: 7, role: 'assistant', warnings: ['careful'] });
    expect(answer.citations[0].verified).toBe(true);
    expect(store.conversations()[0].id).toBe('c1');
  });

  it('shows the text as it streams in and drops what came before a tool call', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    api.ask = async function* () {
      yield { event: 'text', data: { delta: 'Let me look. ' } } as ChatEvent;
      yield { event: 'text_reset', data: {} } as ChatEvent;
      yield {
        event: 'tool_call',
        data: { id: 't1', name: 'grep_code', arguments: {} },
      } as ChatEvent;
      yield { event: 'text', data: { delta: 'It ' } } as ChatEvent;
      yield { event: 'text', data: { delta: 'does X' } } as ChatEvent;
      await gate;
      yield DONE;
    } as never;
    const pending = store.send('q');
    await vi.waitFor(() => expect(store.turn()?.text).toBe('It does X'));
    expect(store.turn()?.steps).toHaveLength(1);
    release();
    await pending;
    expect(store.messages().at(-1)?.content).toBe('It does X `a/b.py:1-2`.');
  });

  it('shows tool progress while the turn runs', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    api.ask = async function* () {
      yield {
        event: 'tool_call',
        data: { id: 't1', name: 'search_code', arguments: {} },
      } as ChatEvent;
      await gate;
      yield DONE;
    } as never;

    const pending = store.send('q');
    await vi.waitFor(() => expect(store.turn()?.steps).toHaveLength(1));
    expect(store.busy()).toBe(true);
    expect(store.turn()?.steps[0]).toMatchObject({ name: 'search_code', status: 'running' });
    release();
    await pending;
    expect(store.busy()).toBe(false);
  });

  it('keeps the question and offers a retry when the model fails', async () => {
    api.events = [{ event: 'error', data: { code: 'provider_error', message: 'overloaded' } }];
    await store.send('q');
    expect(store.turn()).toMatchObject({ state: 'error', error: 'overloaded' });
    expect(store.messages().map((m) => m.role)).toEqual(['user']);

    api.events = [DONE];
    await store.retry();
    expect(api.asked).toEqual(['q', 'q']);
    expect(store.turn()).toBeNull();
    expect(store.messages().map((m) => m.role)).toEqual(['user', 'assistant']);
  });

  it('reports an unreachable server instead of losing the question', async () => {
    api.failWith = new ApiError(0, 'The server could not be reached.');
    await store.send('q');
    expect(store.turn()?.error).toBe('The server could not be reached.');
  });

  it('treats a stream that ends without an answer as an interruption', async () => {
    api.events = [{ event: 'text', data: { delta: 'partial' } }];
    await store.send('q');
    expect(store.turn()).toMatchObject({ state: 'error', text: 'partial' });
  });

  it('stopping drops the turn quietly', async () => {
    api.ask = async function* (_id: string, _c: string, signal?: AbortSignal) {
      yield { event: 'tool_call', data: { id: 't1', name: 'x', arguments: {} } } as ChatEvent;
      await new Promise((resolve) => signal?.addEventListener('abort', resolve));
    } as never;
    const pending = store.send('q');
    await vi.waitFor(() => expect(store.busy()).toBe(true));
    store.stop();
    await pending;
    expect(store.turn()).toBeNull();
  });

  it('ignores an empty question and a second one while busy', async () => {
    await store.send('   ');
    expect(api.asked).toEqual([]);
    store.turn.set({ question: 'a', steps: [], text: '', state: 'running' });
    await store.send('b');
    expect(api.asked).toEqual([]);
  });

  it('changes the draft before a conversation exists and the conversation after', async () => {
    await store.setScope(['a']);
    await store.setProfile('functional');
    expect(store.scope()).toEqual(['a']);
    expect(store.profile()).toBe('functional');
    expect(api.updates).toEqual([]);

    store.current.set(conversation());
    await store.setScope(['a']);
    expect(api.updates).toEqual([{ applications: ['a'] }]);
    expect(store.scope()).toEqual(['a']);
    await store.setScope(null);
    expect(api.updates[1]).toEqual({ all_applications: true });
    expect(store.scope()).toBeNull();
  });

  it('opens a conversation, and says so when it no longer exists', async () => {
    await store.open('c1');
    expect(store.current()?.id).toBe('c1');
    api.conversation = async () => {
      throw new ApiError(404, 'Conversation not found.');
    };
    await store.open('gone');
    expect(store.current()).toBeNull();
    expect(store.conversationError()).toBe('This conversation no longer exists.');
  });

  it('removes a conversation from the list and closes it if it was open', async () => {
    store.conversations.set([conversation(), conversation({ id: 'c2' })]);
    store.current.set(conversation());
    expect(await store.remove('c1')).toBe(true);
    expect(store.conversations().map((c) => c.id)).toEqual(['c2']);
    expect(store.current()).toBeNull();
  });

  it('flagging an answer returns the feedback id, or a problem instead of throwing', async () => {
    expect(await store.flag(7, 'incorrect', 'x')).toEqual({ id: 1 });
    api.feedback = async () => {
      throw new ApiError(422, 'Only answers can be flagged.');
    };
    expect(await store.flag(1, 'incomplete', null)).toEqual({
      error: 'Only answers can be flagged.',
    });
  });

  it('proposes a correction and reports why it was refused', async () => {
    expect(await store.propose(1, 'a', 'purpose', 'It is X.')).toBeNull();
    expect(api.proposals).toEqual([[1, 'a', 'purpose', 'It is X.']]);
    api.proposeCorrection = async () => {
      throw new ApiError(422, 'This feedback already has a proposal waiting.');
    };
    expect(await store.propose(1, 'a', 'purpose', null)).toBe(
      'This feedback already has a proposal waiting.',
    );
  });

  it('loads the catalog sections once', async () => {
    await store.loadSections();
    await store.loadSections();
    expect(store.sections().map((s) => s.id)).toEqual(['purpose', 'stack']);
    expect(api.sectionCalls).toBe(1);
  });
});
