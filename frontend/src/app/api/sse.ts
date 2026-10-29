export interface SseMessage {
  event: string;
  data: unknown;
}

/** Parses one Server-Sent Events block (`event: x` / `data: {...}` lines); null if it has no data. */
export function parseSseBlock(block: string): SseMessage | null {
  let event = 'message';
  const data: string[] = [];
  for (const rawLine of block.split('\n')) {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
  }
  if (data.length === 0) return null;
  try {
    return { event, data: JSON.parse(data.join('\n')) };
  } catch {
    return null;
  }
}

/** Reads a streamed response body and yields its SSE messages as they complete. */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseMessage> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end: number;
      while ((end = buffer.search(/\r?\n\r?\n/)) !== -1) {
        const separator = buffer.slice(end).match(/^\r?\n\r?\n/)![0].length;
        const message = parseSseBlock(buffer.slice(0, end));
        buffer = buffer.slice(end + separator);
        if (message) yield message;
      }
    }
    buffer += decoder.decode();
    const last = parseSseBlock(buffer);
    if (last) yield last;
  } finally {
    reader.releaseLock();
  }
}
