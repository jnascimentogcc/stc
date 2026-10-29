import { TestBed } from '@angular/core/testing';
import { Highlighter, languageForFence, languageForFile } from './highlight';

describe('languages', () => {
  it('are found by file name', () => {
    expect(languageForFile('src/app/client.ts')).toBe('typescript');
    expect(languageForFile('billing/api.PY')).toBe('python');
    expect(languageForFile('db/Dockerfile')).toBe('dockerfile');
    expect(languageForFile('Dockerfile.dev')).toBe('dockerfile');
    expect(languageForFile('config/app.yml')).toBe('yaml');
    expect(languageForFile('LICENSE')).toBeNull();
    expect(languageForFile('data.unknownext')).toBeNull();
  });

  it('are found by the name after the fence', () => {
    expect(languageForFence('ts')).toBe('typescript');
    expect(languageForFence(' Python ')).toBe('python');
    expect(languageForFence('sh')).toBe('bash');
    expect(languageForFence('c#')).toBe('csharp');
    expect(languageForFence('klingon')).toBeNull();
  });
});

describe('Highlighter', () => {
  const highlighter = () => TestBed.inject(Highlighter);

  it('colors the tokens of each line with theme variables, keeping every character', async () => {
    const code = 'def greet(name):\n    return f"Hello, {name}!"';
    const lines = await highlighter().tokens(code, 'python');
    expect(lines).toHaveLength(2);
    expect(lines!.map((line) => line.map((t) => t.text).join(''))).toEqual(code.split('\n'));
    const keyword = lines![0].find((t) => t.text === 'def');
    expect(keyword?.color).toBe('var(--shiki-token-keyword)');
    expect(lines![0].some((t) => t.color === 'var(--shiki-token-function)')).toBe(true);
  });

  it('works for several languages with one highlighter', async () => {
    const ts = await highlighter().tokens('const a: number = 1;', 'typescript');
    const json = await highlighter().tokens('{"a": 1}', 'json');
    expect(ts?.[0].map((t) => t.text).join('')).toBe('const a: number = 1;');
    expect(json?.[0].map((t) => t.text).join('')).toBe('{"a": 1}');
  });

  it('gives null where there is nothing to highlight with', async () => {
    expect(await highlighter().tokens('x', null)).toBeNull();
    expect(await highlighter().tokens('x', 'klingon')).toBeNull();
  });
});
