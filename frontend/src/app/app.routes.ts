import { Routes } from '@angular/router';
import { ChatPage } from './chat/chat-page';
import { DocsPage } from './docs/docs-page';

export const routes: Routes = [
  { path: '', component: ChatPage },
  { path: 'c/:id', component: ChatPage },
  { path: 'docs', component: DocsPage },
  { path: 'docs/:app', component: DocsPage },
  { path: '**', redirectTo: '' },
];
