import { DOCUMENT } from '@angular/common';
import { Injectable, computed, effect, inject, signal } from '@angular/core';

export type Theme = 'light' | 'dark';

/** Light/dark theme: follows the system preference until the user picks one (ui-spec §11). */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly root = inject(DOCUMENT).documentElement;
  private readonly _theme = signal<Theme>(this.initial());

  readonly theme = this._theme.asReadonly();
  readonly isDark = computed(() => this._theme() === 'dark');

  constructor() {
    effect(() => {
      this.root.dataset['theme'] = this._theme();
    });
  }

  toggle(): void {
    const next: Theme = this.isDark() ? 'light' : 'dark';
    this._theme.set(next);
    try {
      localStorage.setItem('stc-theme', next);
    } catch {
      // Storage unavailable: the choice just lasts for this session.
    }
  }

  private initial(): Theme {
    try {
      const saved = localStorage.getItem('stc-theme');
      if (saved === 'light' || saved === 'dark') return saved;
    } catch {
      // Ignore and fall back to the system preference.
    }
    return globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
}
