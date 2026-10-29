import { parseSseBlock, readSse } from './sse';

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const out = [];
  for await (const message of readSse(stream)) out.push(message);
  return out;
}

describe('parseSseBlock', () => {
  it('reads the event name and the JSON data', () => {
    expect(parseSseBlock('event: text\ndata: {"delta":"hi"}')).toEqual({
      event: 'text',
      data: { delta: 'hi' },
    });
  });

  it('ignores blocks without data or with data that is not JSON', () => {
    expect(parseSseBlock(': keep-alive')).toBeNull();
    expect(parseSseBlock('event: text\ndata: nope')).toBeNull();
  });
});

describe('readSse', () => {
  it('yields each event as soon as its block ends, even when chunks split it', async () => {
    const messages = await collect(
      streamOf(
        'event: tool_call\ndata: {"id":"1"}\n\nevent: te',
        'xt\ndata: {"delta":"a ',
        'b"}\n\n',
        'event: done\ndata: {"answer":"ok"}\n\n',
      ),
    );
    expect(messages.map((m) => m.event)).toEqual(['tool_call', 'text', 'done']);
    expect(messages[1].data).toEqual({ delta: 'a b' });
  });

  it('handles CRLF line endings and a final block without a blank line', async () => {
    const messages = await collect(
      streamOf('event: text\r\ndata: {"delta":"x"}\r\n\r\nevent: done\r\ndata: {"answer":"y"}'),
    );
    expect(messages.map((m) => m.event)).toEqual(['text', 'done']);
  });

  it('keeps multi-byte characters that arrive split across chunks', async () => {
    const bytes = new TextEncoder().encode('event: text\ndata: {"delta":"é"}\n\n');
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 29));
        controller.enqueue(bytes.slice(29));
        controller.close();
      },
    });
    expect((await collect(stream))[0].data).toEqual({ delta: 'é' });
  });
});
