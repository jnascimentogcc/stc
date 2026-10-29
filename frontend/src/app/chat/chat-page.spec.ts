import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ChatApi } from '../api/chat-api';
import { ChatEvent, Citation, Message } from '../api/models';
import { provideTestTransloco } from '../testing';
import { ChatPage } from './chat-page';
import { ChatStore } from './chat-store';
import { CitationListComponent } from './citation-list';
import { MessageViewComponent } from './message-view';

const text = (element: Element) => (element.textContent ?? '').replace(/\s+/g, ' ');

function api(overrides: Partial<Record<keyof ChatApi, unknown>> = {}) {
  return {
    provide: ChatApi,
    useValue: {
      applications: async () => [
        { name: 'demo-api', has_catalog: true },
        { name: 'demo-web', has_catalog: false },
      ],
      conversations: async () => [],
      conversation: async (id: string) => ({
        id,
        title: 'A title',
        scope: { type: 'root' },
        profile: 'technical',
        created_at: '',
        updated_at: '',
        messages: [],
      }),
      createConversation: async () => ({
        id: 'c1',
        title: null,
        scope: { type: 'root' },
        profile: 'technical',
        created_at: '',
        updated_at: '',
      }),
      updateConversation: async () => ({}),
      deleteConversation: async () => undefined,
      feedback: async () => ({ id: 1 }),
      catalogSections: async () => [
        { id: 'purpose', title: 'Functional purpose' },
        { id: 'stack', title: 'Stack and versions' },
      ],
      proposeCorrection: async () => ({ id: 1 }),
      fileSlice: async () => ({
        source: {
          application: 'demo-web',
          file: 'src/client.ts',
          start_line: 2,
          end_line: 3,
          code_version: 'abcdef123',
          ref: 'x',
        },
        text: 'const a = 1;\nconst b = 2;',
        total_lines: 9,
        truncated: false,
        next_start_line: null,
        stale: false,
      }),
      async *ask(): AsyncGenerator<ChatEvent> {
        yield {
          event: 'tool_call',
          data: { id: 't', name: 'get_dependencies', arguments: { application: 'demo-api' } },
        };
        yield {
          event: 'tool_result',
          data: { id: 't', name: 'get_dependencies', ok: true, summary: '1 result(s)' },
        };
        yield {
          event: 'done',
          data: {
            answer: '**demo-web** calls it.',
            citations: [
              {
                ref: 'demo-web/src/client.ts:2-3',
                verified: true,
                application: 'demo-web',
                file: 'src/client.ts',
                start_line: 2,
                end_line: 3,
              },
              {
                ref: 'demo-api/ghost.py:1-2',
                verified: false,
                application: null,
                file: null,
                start_line: 1,
                end_line: 2,
              },
            ],
            warnings: [],
            steps: 2,
            usage: { input_tokens: 1, output_tokens: 1 },
            message_id: 5,
          },
        };
      },
      ...overrides,
    },
  };
}

describe('ChatPage', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([
          { path: '', children: [] },
          { path: 'c/:id', children: [] },
        ]),
        provideTestTransloco(),
        api(),
      ],
    });
  });

  it('starts with an explanation and a composer', async () => {
    const fixture = TestBed.createComponent(ChatPage);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(text(root)).toContain('Ask about your applications');
    expect(root.querySelector('textarea#question')).not.toBeNull();
    expect(root.querySelector('button[type=submit]')?.hasAttribute('disabled')).toBe(true);
  });

  it('sends a question with Enter and shows the tool steps, the answer and its sources', async () => {
    const fixture = TestBed.createComponent(ChatPage);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const field = root.querySelector('textarea#question') as HTMLTextAreaElement;
    field.value = 'Who calls demo-api?';
    field.dispatchEvent(new Event('input'));
    field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', cancelable: true }));
    await vi.waitFor(() => expect(TestBed.inject(ChatStore).messages()).toHaveLength(2));
    await fixture.whenStable();

    const content = text(root);
    expect(content).toContain('Who calls demo-api?');
    expect(content).toContain('demo-web calls it.');
    expect(root.querySelector('article strong')?.textContent).toBe('demo-web');
    expect(content).toContain('demo-web/src/client.ts:2-3');
    expect(content).toContain('demo-api/ghost.py:1-2');
    expect(content).toContain('unverified');
    expect(content).toContain('Some sources could not be matched');
    expect(field.value).toBe('');
  });

  it('does not send on Shift+Enter', async () => {
    const fixture = TestBed.createComponent(ChatPage);
    await fixture.whenStable();
    const field = (fixture.nativeElement as HTMLElement).querySelector(
      'textarea#question',
    ) as HTMLTextAreaElement;
    field.value = 'a';
    field.dispatchEvent(new Event('input'));
    field.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, cancelable: true }),
    );
    await fixture.whenStable();
    expect(TestBed.inject(ChatStore).messages()).toHaveLength(0);
  });

  it('lists the applications to restrict the scope to', async () => {
    const fixture = TestBed.createComponent(ChatPage);
    await fixture.whenStable();
    const store = TestBed.inject(ChatStore);
    await store.setScope(['demo-api']);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const boxes = [...root.querySelectorAll<HTMLInputElement>('input[type=checkbox]')];
    expect(boxes.map((b) => [text(b.parentElement!).trim(), b.checked])).toEqual([
      ['demo-api', true],
      ['demo-web', false],
    ]);
  });
});

describe('CitationListComponent', () => {
  const verified: Citation = {
    ref: 'demo-web/src/client.ts:2-3',
    verified: true,
    application: 'demo-web',
    file: 'src/client.ts',
    start_line: 2,
    end_line: 3,
  };
  const unverified: Citation = {
    ref: 'x/y.py:1-1',
    verified: false,
    application: null,
    file: null,
    start_line: 1,
    end_line: 1,
  };

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideTestTransloco(), api()] });
  });

  it('opens the cited lines of a verified source, with their numbers', async () => {
    const fixture = TestBed.createComponent(CitationListComponent);
    fixture.componentRef.setInput('citations', [verified, unverified]);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;

    const buttons = root.querySelectorAll('button');
    expect(buttons).toHaveLength(1); // the unverified one is not a control
    buttons[0].click();
    await vi.waitFor(() => expect(root.querySelector('pre')).not.toBeNull());
    await fixture.whenStable();
    const pre = root.querySelector('pre')!;
    expect([...pre.querySelectorAll('span')].map((n) => n.textContent)).toEqual(['2', '3']);
    expect(text(pre)).toContain('const a = 1;');
    expect(text(pre)).toContain('const b = 2;');
    expect(text(root)).toContain('abcdef1');
    expect(buttons[0].getAttribute('aria-expanded')).toBe('true');

    buttons[0].click();
    await fixture.whenStable();
    expect(root.querySelector('pre')).toBeNull();
  });

  it('says so when the code cannot be loaded', async () => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideTestTransloco(),
        api({
          fileSlice: async () => {
            throw new Error('withheld: it may contain secrets.');
          },
        }),
      ],
    });
    const fixture = TestBed.createComponent(CitationListComponent);
    fixture.componentRef.setInput('citations', [verified]);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    root.querySelector('button')!.click();
    await vi.waitFor(() => expect(root.querySelector('[role=alert]')).not.toBeNull());
    expect(text(root)).toContain('withheld');
  });
});

describe('MessageViewComponent feedback', () => {
  const answer: Message = {
    id: 9,
    role: 'assistant',
    content: 'An answer',
    citations: [],
    created_at: '',
  };

  it('asks for a correction and confirms it was saved', async () => {
    const feedback = vi.fn(async () => ({ id: 1 }));
    TestBed.configureTestingModule({ providers: [provideTestTransloco(), api({ feedback })] });
    const fixture = TestBed.createComponent(MessageViewComponent);
    fixture.componentRef.setInput('message', answer);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const click = (label: string) =>
      [...root.querySelectorAll('button')].find((b) => text(b).trim() === label)!.click();

    click('Incorrect');
    await fixture.whenStable();
    const field = root.querySelector('textarea') as HTMLTextAreaElement;
    field.value = 'It is Y';
    field.dispatchEvent(new Event('input'));
    click('Send feedback');
    await vi.waitFor(() => expect(text(root)).toContain('your feedback was saved'));
    expect(feedback).toHaveBeenCalledWith(9, 'incorrect', 'It is Y');
  });

  it('can also propose the correction for a catalog section, once a text is written', async () => {
    const feedback = vi.fn(async () => ({ id: 41 }));
    const proposeCorrection = vi.fn(async () => ({ id: 1 }));
    TestBed.configureTestingModule({
      providers: [provideTestTransloco(), api({ feedback, proposeCorrection })],
    });
    TestBed.inject(ChatStore).applications.set([
      { name: 'demo-api', has_catalog: true },
      { name: 'demo-web', has_catalog: true },
    ]);
    const fixture = TestBed.createComponent(MessageViewComponent);
    fixture.componentRef.setInput('message', {
      ...answer,
      citations: [
        {
          ref: 'demo-web/a.ts:1-2',
          verified: true,
          application: 'demo-web',
          file: 'a.ts',
          start_line: 1,
          end_line: 2,
        },
      ],
    });
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    [...root.querySelectorAll('button')].find((b) => text(b).trim() === 'Incorrect')!.click();
    await fixture.whenStable();
    expect(root.querySelector('fieldset')).toBeNull(); // nothing to propose before a text exists

    const field = root.querySelector('textarea') as HTMLTextAreaElement;
    field.value = 'It is Y';
    field.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    const toggle = root.querySelector('fieldset input[type=checkbox]') as HTMLInputElement;
    toggle.checked = true;
    toggle.dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(root.querySelectorAll('fieldset select')).toHaveLength(2));
    await fixture.whenStable();
    const [application, section] = [
      ...root.querySelectorAll('fieldset select'),
    ] as HTMLSelectElement[];
    expect(application.value).toBe('demo-web'); // the application the answer cited
    expect(section.value).toBe('purpose');
    section.value = 'stack';
    section.dispatchEvent(new Event('change'));

    root.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await vi.waitFor(() => expect(text(root)).toContain('waits for a maintainer to approve'));
    expect(feedback).toHaveBeenCalledWith(9, 'incorrect', 'It is Y');
    expect(proposeCorrection).toHaveBeenCalledWith(41, 'demo-web', 'stack', 'It is Y');
  });

  it('keeps the feedback and says why when the proposal is refused', async () => {
    TestBed.configureTestingModule({
      providers: [
        provideTestTransloco(),
        api({
          proposeCorrection: async () => {
            throw new Error('refused');
          },
        }),
      ],
    });
    TestBed.inject(ChatStore).applications.set([{ name: 'demo-api', has_catalog: true }]);
    const fixture = TestBed.createComponent(MessageViewComponent);
    fixture.componentRef.setInput('message', answer);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    [...root.querySelectorAll('button')].find((b) => text(b).trim() === 'Incorrect')!.click();
    await fixture.whenStable();
    const field = root.querySelector('textarea') as HTMLTextAreaElement;
    field.value = 'It is Y';
    field.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    const toggle = root.querySelector('fieldset input[type=checkbox]') as HTMLInputElement;
    toggle.checked = true;
    toggle.dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(root.querySelectorAll('fieldset select')).toHaveLength(2));
    root.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await vi.waitFor(() => expect(text(root)).toContain('your feedback was saved'));
    expect(root.querySelector('[role=alert]')).not.toBeNull();
    expect(text(root)).not.toContain('waits for a maintainer');
  });

  it('shows the problem and keeps the form when saving fails', async () => {
    TestBed.configureTestingModule({
      providers: [
        provideTestTransloco(),
        api({
          feedback: async () => {
            throw new Error('boom');
          },
        }),
      ],
    });
    const fixture = TestBed.createComponent(MessageViewComponent);
    fixture.componentRef.setInput('message', answer);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    [...root.querySelectorAll('button')].find((b) => text(b).trim() === 'Incomplete')!.click();
    await fixture.whenStable();
    (root.querySelector('form') as HTMLFormElement).dispatchEvent(
      new Event('submit', { cancelable: true }),
    );
    await vi.waitFor(() => expect(root.querySelector('[role=alert]')).not.toBeNull());
    expect(root.querySelector('textarea')).not.toBeNull();
  });
});
