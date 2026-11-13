export type DocsStatus = 'complete' | 'partial' | 'static';

/** An indexed application and the state of its documentation (DOC-11). */
export interface DocApp {
  name: string;
  has_docs: boolean;
  status?: DocsStatus;
  generated_at?: string;
  code_version?: string | null;
  outdated?: boolean;
  pages?: number;
  model?: string;
}

export interface DocPageRef {
  path: string;
  title: string;
  group: string;
}

export interface AppDocs {
  name: string;
  status: DocsStatus;
  generated_at: string;
  code_version: string | null;
  outdated: boolean;
  model: string;
  language: string;
  pages: DocPageRef[];
}

export interface DocPage {
  application: string;
  path: string;
  title: string;
  group: string;
  content: string;
}

/** A `file:lines` reference found in a page, to open the code behind it. */
export interface DocCitation {
  ref: string;
  application: string;
  file: string;
  start_line: number;
  end_line: number;
}

export interface DocHeading {
  id: string;
  text: string;
  level: number;
}
