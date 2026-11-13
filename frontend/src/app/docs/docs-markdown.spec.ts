import { renderDoc, resolvePagePath } from './docs-markdown';

const context = { application: 'loans', path: 'features/pay.md', applications: ['loans', 'other'] };

describe('resolvePagePath', () => {
  it('resolves relative links against the page', () => {
    expect(resolvePagePath('features/pay.md', '../data-model.md')).toBe('data-model.md');
    expect(resolvePagePath('features/pay.md', 'other.md')).toBe('features/other.md');
  });

  it('ignores what is not a page of the documentation', () => {
    expect(resolvePagePath('a.md', 'https://example.com/x.md')).toBeNull();
    expect(resolvePagePath('a.md', '/x.md')).toBeNull();
    expect(resolvePagePath('a.md', 'x.png')).toBeNull();
    expect(resolvePagePath('a.md', '../x.md')).toBeNull();
  });
});

describe('renderDoc', () => {
  it('gives headings unique ids and lists them', () => {
    const { html, headings } = renderDoc('# Title\n\n## Part\n\n## Part', context);
    expect(headings.map((h) => h.id)).toEqual(['h-title', 'h-part', 'h-part-2']);
    expect(html).toContain('id="h-part-2"');
  });

  it('turns links between pages into viewer links', () => {
    const { html } = renderDoc('[data](../data-model.md#tables)', context);
    expect(html).toContain('data-doc-path="data-model.md"');
    expect(html).toContain('data-doc-anchor="tables"');
    expect(html).toContain('href="/docs/loans?page=data-model.md"');
  });

  it('marks file:lines references, including those of another documented application', () => {
    const { html } = renderDoc('`src/a.cbl:10-20` and `other/b.py:3`', context);
    expect(html).toContain('data-file="src/a.cbl"');
    expect(html).toContain('data-start="10"');
    expect(html).toContain('data-app="other"');
    expect(html).toContain('data-file="b.py"');
  });

  it('does not mark plain code or reversed ranges', () => {
    const { html } = renderDoc('`foo` `a.py:9-2`', context);
    expect(html).not.toContain('cite-ok');
  });

  it('wraps mermaid blocks for rendering and sanitizes the rest', () => {
    const { html } = renderDoc(
      '```mermaid\nflowchart TD\n A-->B\n```\n\n<script>x</script>',
      context,
    );
    expect(html).toContain('<figure class="diagram"');
    expect(html).toContain('data-diagram="flowchart TD');
    expect(html).not.toContain('<script');
  });
});
