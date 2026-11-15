/**
 * The shared "code block" of rendered markdown (ui-spec §6.8): a dark block with a bar that shows
 * the language and a copy button. It works on the HTML a component already rendered and sanitized,
 * adding only elements and attributes this app chooses, so no external content becomes markup.
 */

export interface CodeBlockLabels {
  copy: string;
  copied: string;
}

const COPIED_FOR_MS = 2000;

function languageOf(code: Element): string {
  const fence = Array.from(code.classList).find((c) => c.startsWith('language-'));
  return fence ? fence.slice('language-'.length) : 'text';
}

/** Wraps every `pre > code` of `root` in a `.code-block` with its bar (once; diagrams excluded). */
export function decorateCodeBlocks(root: HTMLElement, labels: CodeBlockLabels): void {
  for (const pre of Array.from(root.querySelectorAll('pre'))) {
    const code = pre.querySelector(':scope > code');
    if (!code || pre.closest('.code-block') || pre.closest('figure.diagram')) continue;

    const bar = document.createElement('div');
    bar.className = 'code-block-bar';
    const language = document.createElement('span');
    language.className = 'code-block-lang';
    language.textContent = languageOf(code);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'code-block-copy';
    button.textContent = labels.copy;
    button.dataset['copyLabel'] = labels.copy;
    button.dataset['copiedLabel'] = labels.copied;
    bar.append(language, button);

    const block = document.createElement('div');
    block.className = 'code-block';
    pre.replaceWith(block);
    block.append(bar, pre);
  }
}

/** Handles a click on a copy button. Returns true when the event was one. */
export function copyCodeFromClick(event: Event): boolean {
  const button = (event.target as HTMLElement | null)?.closest<HTMLButtonElement>(
    'button.code-block-copy',
  );
  const text = button?.closest('.code-block')?.querySelector('pre')?.textContent;
  if (!button || text === null || text === undefined) return false;
  void navigator.clipboard?.writeText(text.replace(/\n$/, '')).then(
    () => {
      button.textContent = button.dataset['copiedLabel'] ?? '';
      button.dataset['copied'] = '';
      setTimeout(() => {
        button.textContent = button.dataset['copyLabel'] ?? '';
        delete button.dataset['copied'];
      }, COPIED_FOR_MS);
    },
    () => undefined,
  );
  return true;
}
