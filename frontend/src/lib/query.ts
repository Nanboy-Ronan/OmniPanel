import { QueryClient } from '@tanstack/react-query';
import { ApiError } from './api';

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60000,
        gcTime: 300000,
        refetchOnWindowFocus: false,
        // Only transient server/network failures get one automatic read retry.
        retry: (count, error) =>
          count < 1 &&
          error instanceof ApiError &&
          (error.status >= 500 || (error.status === 0 && /连接|超时/.test(error.message))),
        retryDelay: 1000,
      },
      mutations: { retry: false },
    },
  });
}
