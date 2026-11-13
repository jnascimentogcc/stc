import { Injectable } from '@angular/core';
import DOMPurify from 'dompurify';

type Mermaid = (typeof import('mermaid'))['default'];

function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/**
 * Draws the Mermaid diagrams of the documentation (DOC-04, DOC-11). Mermaid is a lazy chunk: it
 * is downloaded the first time a diagram is shown. A diagram comes from generated markdown, so it
 * is treated as untrusted: Mermaid runs in `strict` mode with plain-text labels, and the SVG it
 * returns is sanitized again before it is put in the page. The colors come from the app's tokens,
 * so diagrams follow the light and dark themes.
 */
@Injectable({ providedIn: 'root' })
export class DiagramRenderer {
  private mermaid: Promise<Mermaid> | null = null;
  private counter = 0;

  private load(): Promise<Mermaid> {
    this.mermaid ??= import('mermaid').then((module) => module.default);
    return this.mermaid;
  }

  /** The diagram as a sanitized SVG fragment. Rejects if the source is not a valid diagram. */
  async render(source: string): Promise<DocumentFragment> {
    const mermaid = await this.load();
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      htmlLabels: false,
      theme: 'base',
      fontFamily: token('--sans', 'sans-serif'),
      flowchart: { htmlLabels: false, useMaxWidth: true },
      themeVariables: {
        background: token('--surface', '#ffffff'),
        primaryColor: token('--surface-2', '#f1ece2'),
        primaryTextColor: token('--text', '#1f2532'),
        primaryBorderColor: token('--accent', '#4e615b'),
        secondaryColor: token('--accent-soft', '#e4ddcf'),
        tertiaryColor: token('--surface', '#f8f5ee'),
        lineColor: token('--text-2', '#4e615b'),
        textColor: token('--text', '#1f2532'),
        edgeLabelBackground: token('--surface', '#f8f5ee'),
        clusterBkg: token('--surface', '#f8f5ee'),
      },
    });
    const { svg } = await mermaid.render(`stc-diagram-${++this.counter}`, source);
    return DOMPurify.sanitize(svg, {
      USE_PROFILES: { svg: true, svgFilters: true },
      RETURN_DOM_FRAGMENT: true,
    });
  }
}
