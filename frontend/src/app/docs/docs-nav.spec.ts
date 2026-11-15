import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { BreadcrumbsComponent } from '../ui/breadcrumbs';
import { DocsTreeComponent } from './docs-tree';
import { PageNavComponent } from './page-nav';
import { provideTestTransloco } from '../testing';

const pages = [
  { path: 'overview.md', title: 'Overview', group: 'Start' },
  { path: 'features/pay.md', title: 'Pay', group: 'Features' },
  { path: 'features/ship.md', title: 'Ship', group: 'Features' },
];
const groups = [
  { name: 'Start', pages: [pages[0]] },
  { name: 'Features', pages: [pages[1], pages[2]] },
];

@Component({
  imports: [DocsTreeComponent],
  template: `<app-docs-tree
    app="shop"
    [groups]="groups"
    [current]="current"
    [filtering]="filtering"
  />`,
})
class TreeHost {
  groups = groups;
  current: string | undefined = 'features/pay.md';
  filtering = false;
}

describe('docs navigation', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([]), ...provideTestTransloco()] });
  });

  it('opens the group of the current page only and marks the page', () => {
    const fixture = TestBed.createComponent(TreeHost);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    const buttons = el.querySelectorAll('button');
    expect(buttons[0].getAttribute('aria-expanded')).toBe('false');
    expect(buttons[1].getAttribute('aria-expanded')).toBe('true');
    expect(el.querySelector('[aria-current="page"]')?.textContent?.trim()).toBe('Pay');
    expect(el.textContent).not.toContain('Overview');
  });

  it('toggles a group with the keyboard-reachable button', () => {
    const fixture = TestBed.createComponent(TreeHost);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    el.querySelectorAll('button')[0].click();
    fixture.detectChanges();
    expect(el.textContent).toContain('Overview');
  });

  it('opens every group while filtering', () => {
    const fixture = TestBed.createComponent(TreeHost);
    fixture.componentInstance.filtering = true;
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Overview');
  });

  it('renders breadcrumbs with the current page last', () => {
    const fixture = TestBed.createComponent(BreadcrumbsComponent);
    fixture.componentRef.setInput('label', 'Breadcrumbs');
    fixture.componentRef.setInput('items', [
      { label: 'shop', link: ['/docs', 'shop'] },
      { label: 'Features' },
      { label: 'Pay' },
    ]);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelectorAll('a').length).toBe(1);
    expect(el.querySelector('[aria-current="page"]')?.textContent?.trim()).toBe('Pay');
  });

  it('links to the previous and next page', () => {
    const fixture = TestBed.createComponent(PageNavComponent);
    fixture.componentRef.setInput('app', 'shop');
    fixture.componentRef.setInput('previous', pages[0]);
    fixture.componentRef.setInput('next', pages[2]);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('Overview');
    expect(text).toContain('Ship');
  });
});
