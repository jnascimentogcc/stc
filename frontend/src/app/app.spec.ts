import { TestBed } from '@angular/core/testing';
import { provideTransloco, TranslocoLoader } from '@jsverse/transloco';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { App } from './app';

class TestLoader implements TranslocoLoader {
  getTranslation() {
    return of({
      app: { name: 'STC', tagline: 'tagline', placeholder: 'placeholder', toggleTheme: 'toggle' },
    });
  }
}

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([]),
        provideTransloco({
          config: { availableLangs: ['en'], defaultLang: 'en' },
          loader: TestLoader,
        }),
      ],
    }).compileComponents();
  });

  it('renders the app name and placeholder', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('STC');
    expect(text).toContain('placeholder');
    expect(
      (fixture.nativeElement as HTMLElement).querySelector('button')?.getAttribute('aria-label'),
    ).toBe('toggle');
  });
});
