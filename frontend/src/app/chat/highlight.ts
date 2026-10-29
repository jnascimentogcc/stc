import { Injectable } from '@angular/core';
import type { HighlighterCore } from 'shiki/core';

export interface Token {
  text: string;
  color?: string;
  italic?: boolean;
  bold?: boolean;
}

/** Each language is its own lazy chunk: the editor-grade grammars load only when code is shown. */
const LOADERS: Record<string, () => Promise<unknown>> = {
  bash: () => import('shiki/langs/shellscript.mjs'),
  c: () => import('shiki/langs/c.mjs'),
  cpp: () => import('shiki/langs/cpp.mjs'),
  csharp: () => import('shiki/langs/csharp.mjs'),
  css: () => import('shiki/langs/css.mjs'),
  dockerfile: () => import('shiki/langs/dockerfile.mjs'),
  go: () => import('shiki/langs/go.mjs'),
  html: () => import('shiki/langs/html.mjs'),
  ini: () => import('shiki/langs/ini.mjs'),
  java: () => import('shiki/langs/java.mjs'),
  javascript: () => import('shiki/langs/javascript.mjs'),
  json: () => import('shiki/langs/json.mjs'),
  jsx: () => import('shiki/langs/jsx.mjs'),
  kotlin: () => import('shiki/langs/kotlin.mjs'),
  markdown: () => import('shiki/langs/markdown.mjs'),
  php: () => import('shiki/langs/php.mjs'),
  properties: () => import('shiki/langs/properties.mjs'),
  python: () => import('shiki/langs/python.mjs'),
  ruby: () => import('shiki/langs/ruby.mjs'),
  rust: () => import('shiki/langs/rust.mjs'),
  sql: () => import('shiki/langs/sql.mjs'),
  toml: () => import('shiki/langs/toml.mjs'),
  tsx: () => import('shiki/langs/tsx.mjs'),
  typescript: () => import('shiki/langs/typescript.mjs'),
  xml: () => import('shiki/langs/xml.mjs'),
  yaml: () => import('shiki/langs/yaml.mjs'),
};

const BY_EXTENSION: Record<string, string> = {
  bash: 'bash',
  c: 'c',
  cc: 'cpp',
  cjs: 'javascript',
  cpp: 'cpp',
  cs: 'csharp',
  css: 'css',
  go: 'go',
  h: 'c',
  hpp: 'cpp',
  html: 'html',
  ini: 'ini',
  java: 'java',
  js: 'javascript',
  json: 'json',
  jsx: 'jsx',
  kt: 'kotlin',
  kts: 'kotlin',
  md: 'markdown',
  mjs: 'javascript',
  php: 'php',
  properties: 'properties',
  py: 'python',
  rb: 'ruby',
  rs: 'rust',
  sh: 'bash',
  sql: 'sql',
  toml: 'toml',
  ts: 'typescript',
  tsx: 'tsx',
  xml: 'xml',
  yaml: 'yaml',
  yml: 'yaml',
};

/** The names people put after ``` in a fenced block. */
const BY_FENCE: Record<string, string> = {
  ...BY_EXTENSION,
  'c#': 'csharp',
  csharp: 'csharp',
  dockerfile: 'dockerfile',
  javascript: 'javascript',
  kotlin: 'kotlin',
  python: 'python',
  ruby: 'ruby',
  rust: 'rust',
  shell: 'bash',
  typescript: 'typescript',
  zsh: 'bash',
};

export function languageForFile(path: string): string | null {
  const name = path.split('/').pop() ?? path;
  if (name === 'Dockerfile' || name.startsWith('Dockerfile.')) return 'dockerfile';
  const dot = name.lastIndexOf('.');
  return dot < 0 ? null : (BY_EXTENSION[name.slice(dot + 1).toLowerCase()] ?? null);
}

export function languageForFence(name: string): string | null {
  return BY_FENCE[name.trim().toLowerCase()] ?? null;
}

/**
 * Syntax highlighting with Shiki, loaded on first use. Colors are CSS variables (`code.css`), so
 * the light and dark themes need no second pass. A language without a grammar, or any failure,
 * gives `null` and the caller shows the plain text.
 */
@Injectable({ providedIn: 'root' })
export class Highlighter {
  private core: Promise<HighlighterCore> | null = null;
  private readonly loaded = new Set<string>();

  private highlighter(): Promise<HighlighterCore> {
    this.core ??= (async () => {
      const [{ createHighlighterCore, createCssVariablesTheme }, { createJavaScriptRegexEngine }] =
        await Promise.all([import('shiki/core'), import('shiki/engine/javascript')]);
      return createHighlighterCore({
        themes: [createCssVariablesTheme({ name: 'stc', variablePrefix: '--shiki-' })],
        langs: [],
        engine: createJavaScriptRegexEngine(),
      });
    })();
    return this.core;
  }

  /** The tokens of every line of `code`, or null if it cannot be highlighted. */
  async tokens(code: string, language: string | null): Promise<Token[][] | null> {
    const loader = language ? LOADERS[language] : undefined;
    if (!language || !loader) return null;
    try {
      const highlighter = await this.highlighter();
      if (!this.loaded.has(language)) {
        await highlighter.loadLanguage(loader() as never);
        this.loaded.add(language);
      }
      return highlighter.codeToTokensBase(code, { lang: language, theme: 'stc' }).map((line) =>
        line.map((token) => ({
          text: token.content,
          color: token.color,
          italic: !!((token.fontStyle ?? 0) & 1),
          bold: !!((token.fontStyle ?? 0) & 2),
        })),
      );
    } catch {
      return null;
    }
  }
}
