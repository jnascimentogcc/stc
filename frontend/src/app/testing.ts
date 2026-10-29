import { EnvironmentProviders, Provider } from '@angular/core';
import { provideTransloco, TranslocoLoader } from '@jsverse/transloco';
import { of } from 'rxjs';
import en from '../../public/i18n/en.json';

class EnglishLoader implements TranslocoLoader {
  getTranslation() {
    return of(en);
  }
}

/** Transloco with the real English file, so a missing or misspelled key fails a test. */
export function provideTestTransloco(): (Provider | EnvironmentProviders)[] {
  return [
    provideTransloco({
      config: { availableLangs: ['en'], defaultLang: 'en' },
      loader: EnglishLoader,
    }),
  ];
}
