import { TestBed } from '@angular/core/testing';
import { Citation } from '../api/models';
import { MarkdownComponent, linkReferences, renderMarkdown } from './markdown';

describe('renderMarkdown', () => {
  it('renders the usual markdown', () => {
    const html = renderMarkdown('**bold** and `code`\n\n- one\n- two');
    expect(html).toContain('<strong>bold</strong>');
    expect(html).toContain('<code>code</code>');
    expect(html).toContain('<li>two</li>');
  });

  it('removes scripts, event handlers and javascript: links', () => {
    const html = renderMarkdown(
      '<script>alert(1)</script>\n\n<img src=x onerror="alert(2)">\n\n<a href="javascript:alert(3)">x</a>',
    );
    expect(html).not.toContain('<script');
    expect(html).not.toContain('onerror');
    expect(html).not.toContain('javascript:');
  });

  it('opens links in a new tab without access to this page', () => {
    const html = renderMarkdown('[site](https://example.com)');
    expect(html).toContain('href="https://example.com"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it('keeps code blocks as text', () => {
    const html = renderMarkdown('```html\n<script>x</script>\n```');
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('<script>');
  });
});

const cite = (ref: string, verified: boolean): Citation => ({
  ref,
  verified,
  application: verified ? 'a' : null,
  file: verified ? 'b.py' : null,
  start_line: 1,
  end_line: 2,
});

describe('linkReferences', () => {
  const html = renderMarkdown(
    'See `a/b.py:1-2` and `x/y.py:3-4` and `plain` and:\n\n```\na/b.py:1-2\n```',
  );

  it('turns a verified reference into a button and flags an unverified one', () => {
    const out = linkReferences(html, [cite('a/b.py:1-2', true), cite('x/y.py:3-4', false)]);
    const doc = new DOMParser().parseFromString(out, 'text/html');
    const ok = doc.querySelector('code.cite-ok')!;
    expect(ok.getAttribute('role')).toBe('button');
    expect(ok.getAttribute('tabindex')).toBe('0');
    expect(ok.getAttribute('data-ref')).toBe('a/b.py:1-2');
    const bad = doc.querySelector('code.cite-bad')!;
    expect(bad.hasAttribute('role')).toBe(false);
    expect(bad.getAttribute('aria-label')).toBe('x/y.py:3-4 (unverified)');
    expect(doc.querySelectorAll('code.cite')).toHaveLength(2); // `plain` and the fenced block stay
  });

  it('leaves the HTML alone when the answer has no citations', () => {
    expect(linkReferences(html, [])).toBe(html);
  });
});

describe('MarkdownComponent', () => {
  function render(source: string, citations: Citation[], highlight = false) {
    const fixture = TestBed.createComponent(MarkdownComponent);
    fixture.componentRef.setInput('source', source);
    fixture.componentRef.setInput('citations', citations);
    fixture.componentRef.setInput('highlight', highlight);
    const opened: string[] = [];
    fixture.componentInstance.referenceOpened.subscribe((ref) => opened.push(ref));
    return { fixture, opened, root: fixture.nativeElement as HTMLElement };
  }

  it('emits the reference of a verified citation on click and on Enter or Space', async () => {
    const { fixture, opened, root } = render('See `a/b.py:1-2` and `x/y.py:3-4`.', [
      cite('a/b.py:1-2', true),
      cite('x/y.py:3-4', false),
    ]);
    await fixture.whenStable();
    const [ok, bad] = Array.from(root.querySelectorAll('code'));
    const key = (target: Element, name: string) =>
      target.dispatchEvent(
        new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }),
      );
    ok.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    key(ok, 'Enter');
    key(ok, ' ');
    key(ok, 'a');
    bad.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    key(bad, 'Enter');
    expect(opened).toEqual(['a/b.py:1-2', 'a/b.py:1-2', 'a/b.py:1-2']);
  });

  it('highlights fenced code once the text is final, without letting code become markup', async () => {
    const source = '```python\ndef greet(name):\n    return "<b>hi</b>"\n```';
    const { fixture, root } = render(source, [], true);
    await fixture.whenStable();
    await vi.waitFor(() => expect(root.querySelector('pre code span')).not.toBeNull());
    const code = root.querySelector('pre code')!;
    expect(code.textContent).toBe('def greet(name):\n    return "<b>hi</b>"');
    expect(code.querySelector('b')).toBeNull(); // the markup inside the code stayed text
    expect(
      Array.from(code.querySelectorAll('span')).some((s) => s.style.color.includes('shiki')),
    ).toBe(true);
  });

  it('does not highlight while the text is still arriving', async () => {
    const { fixture, root } = render('```python\ndef f(): pass\n```', [], false);
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(root.querySelector('pre code span')).toBeNull();
  });
});
