import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { provideIcons, NgIcon } from '@ng-icons/core';
import { lucideMoon, lucideSun } from '@ng-icons/lucide';
import { TranslocoDirective } from '@jsverse/transloco';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { ThemeService } from './theme';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, TranslocoDirective, NgIcon, ...HlmButtonImports],
  providers: [provideIcons({ lucideMoon, lucideSun })],
  templateUrl: './app.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class App {
  protected readonly theme = inject(ThemeService);
}
