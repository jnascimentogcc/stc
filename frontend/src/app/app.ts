import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router } from '@angular/router';
import { filter, map } from 'rxjs';
import { RouterLink, RouterOutlet } from '@angular/router';
import { provideIcons, NgIcon } from '@ng-icons/core';
import { lucideMoon, lucideSun } from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { ThemeService } from './theme';

@Component({
  selector: 'app-root',
  imports: [RouterLink, RouterOutlet, TranslocoDirective, NgIcon, ...HlmButtonImports],
  providers: [provideIcons({ lucideMoon, lucideSun })],
  templateUrl: './app.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class App {
  protected readonly theme = inject(ThemeService);
  private readonly router = inject(Router);

  /** The part of the app being shown, for the header navigation. */
  protected readonly section = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map(() => (this.router.url.startsWith('/docs') ? 'docs' : 'chat')),
    ),
    { initialValue: this.router.url.startsWith('/docs') ? 'docs' : 'chat' },
  );
}
