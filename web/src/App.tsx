import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { RouterProvider, type createBrowserRouter } from 'react-router';
import { makeQueryClient } from './api/hooks';
import { ProjectProvider } from './api/project';
import { ToastProvider } from './components';

export function Providers({ children, client }: { children: ReactNode; client?: QueryClient }) {
  const [qc] = useState(() => client ?? makeQueryClient());
  return (
    <QueryClientProvider client={qc}>
      <ProjectProvider>
        <ToastProvider>{children}</ToastProvider>
      </ProjectProvider>
    </QueryClientProvider>
  );
}

export function App({ router, client }: { router: ReturnType<typeof createBrowserRouter>; client?: QueryClient }) {
  return (
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>
  );
}
