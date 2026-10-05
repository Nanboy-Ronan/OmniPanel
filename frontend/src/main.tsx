import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import { consumeOAuthCallback } from './lib/auth';
import { createQueryClient } from './lib/query';
import { PageBoundary } from './components/ui';
import './styles.css';

const callback = consumeOAuthCallback();
// App handles the error visibly after mount; attach a handler immediately.
void callback?.catch(() => undefined);
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={createQueryClient()}>
      <PageBoundary>
        <App callback={callback} />
      </PageBoundary>
    </QueryClientProvider>
  </StrictMode>,
);
