import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideTestTransloco } from './testing';
import { App } from './app';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideRouter([]), provideTestTransloco()],
    }).compileComponents();
  });

  it('renders the app name, the tagline and an accessible theme toggle', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.textContent).toContain('STC');
    expect(root.textContent).toContain('Talk is cheap. Show me the code.');
    expect(root.querySelector('button')?.getAttribute('aria-label')).toBe(
      'Toggle light/dark theme',
    );
  });
});
