import { Routes } from '@angular/router';
import { ChatPage } from './chat/chat-page';

export const routes: Routes = [
  { path: '', component: ChatPage },
  { path: 'c/:id', component: ChatPage },
  { path: '**', redirectTo: '' },
];
