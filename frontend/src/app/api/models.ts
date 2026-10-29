export type Profile = 'technical' | 'functional';

export type Scope = { type: 'root' } | { type: 'applications'; applications: string[] };

export interface Conversation {
  id: string;
  title: string | null;
  scope: Scope;
  profile: Profile;
  created_at: string;
  updated_at: string;
}

export interface Citation {
  ref: string;
  verified: boolean;
  application: string | null;
  file: string | null;
  start_line: number | null;
  end_line: number | null;
}

export interface Message {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  citations: Citation[];
  created_at: string;
  /** Only for the answer that was just streamed; warnings are not stored. */
  warnings?: string[];
}

export interface Application {
  name: string;
  has_catalog: boolean;
}

export interface FileSlice {
  source: {
    application: string;
    file: string;
    start_line: number;
    end_line: number;
    code_version: string | null;
    ref: string;
  };
  text: string;
  total_lines: number;
  truncated: boolean;
  next_start_line: number | null;
  stale: boolean;
}

export interface Feedback {
  id: number;
  message_id: number;
  rating: 'incorrect' | 'incomplete';
  correction: string | null;
}

export interface DoneData {
  answer: string;
  citations: Citation[];
  warnings: string[];
  steps: number;
  usage: { input_tokens: number; output_tokens: number };
  message_id: number;
}

export type ChatEvent =
  | { event: 'tool_call'; data: { id: string; name: string; arguments: Record<string, unknown> } }
  | { event: 'tool_result'; data: { id: string; name: string; ok: boolean; summary: string } }
  | { event: 'text'; data: { delta: string } }
  | { event: 'done'; data: DoneData }
  | { event: 'error'; data: { code: string; message: string } };
