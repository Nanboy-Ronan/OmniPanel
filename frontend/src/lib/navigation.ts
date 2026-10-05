import { useSyncExternalStore } from 'react';

function subscribe(listener: () => void) {
  window.addEventListener('popstate', listener);
  window.addEventListener('console-navigation', listener);
  return () => {
    window.removeEventListener('popstate', listener);
    window.removeEventListener('console-navigation', listener);
  };
}
export function useLocationSearch() {
  return useSyncExternalStore(subscribe, () => window.location.search);
}
export function navigate(values: Record<string, string | null>) {
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(values)) {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  }
  window.history.pushState(null, '', url);
  window.dispatchEvent(new Event('console-navigation'));
}
