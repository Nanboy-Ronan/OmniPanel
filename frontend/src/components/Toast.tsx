import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { CircleAlert, CircleCheck, X } from 'lucide-react';

export type ToastTone = 'success' | 'error';
export type Toaster = {
  success: (message: string) => void;
  error: (message: string) => void;
};
type Item = { id: number; tone: ToastTone; message: string };

const silent: Toaster = { success: () => undefined, error: () => undefined };
const ToastContext = createContext<Toaster>(silent);
/** Outside a provider (isolated component tests) notifications are dropped. */
export const useToast = () => useContext(ToastContext);

const LIFETIME: Record<ToastTone, number> = { success: 4000, error: 6000 };
const LIMIT = 4;

/**
 * Transient outcome notices, stacked bottom-right. Inline status and error text stay where they
 * are; a toast only confirms the outcome when the result may be off-screen or in a closed panel.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const region = useRef<HTMLElement>(null);
  const seq = useRef(0);
  const dismiss = useCallback((id: number) => {
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setItems((list) => list.filter((item) => item.id !== id));
  }, []);
  const schedule = useCallback(
    (item: Item) => {
      clearTimeout(timers.current.get(item.id));
      timers.current.set(
        item.id,
        setTimeout(() => dismiss(item.id), LIFETIME[item.tone]),
      );
    },
    [dismiss],
  );
  const push = useCallback(
    (tone: ToastTone, message: string) => {
      const item = { id: ++seq.current, tone, message };
      setItems((list) => {
        const kept = list.slice(-(LIMIT - 1));
        for (const old of list)
          if (!kept.includes(old)) {
            clearTimeout(timers.current.get(old.id));
            timers.current.delete(old.id);
          }
        return [...kept, item];
      });
      schedule(item);
    },
    [schedule],
  );
  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending.values()) clearTimeout(timer);
      pending.clear();
    };
  }, []);
  // A modal dialog sits in the top layer; re-opening the region as a popover keeps new notices
  // above it. Browsers without popover support fall back to the fixed-position stack.
  const count = items.length;
  const newest = items[count - 1]?.id;
  useLayoutEffect(() => {
    const el = region.current;
    if (!el || typeof el.showPopover !== 'function') return;
    // The region stays open (and empty) so the live region exists before the first notice.
    try {
      if (el.matches(':popover-open')) el.hidePopover();
      el.showPopover();
    } catch {
      // Popover state is cosmetic; the notices are still rendered.
    }
  }, [count, newest]);
  const api = useMemo<Toaster>(
    () => ({
      success: (message) => push('success', message),
      error: (message) => push('error', message),
    }),
    [push],
  );
  const pause = () => {
    for (const timer of timers.current.values()) clearTimeout(timer);
  };
  const resume = () => {
    for (const item of items) schedule(item);
  };
  return (
    <ToastContext.Provider value={api}>
      {children}
      <section
        ref={region}
        className="toast-region"
        aria-live="polite"
        aria-label="通知"
        popover="manual"
        onMouseEnter={pause}
        onMouseLeave={resume}
        onFocus={pause}
        onBlur={resume}
      >
        {items.map((item) => {
          const Icon = item.tone === 'error' ? CircleAlert : CircleCheck;
          return (
            <div key={item.id} className={`toast toast-${item.tone}`}>
              <Icon size={17} aria-hidden />
              <p>{item.message}</p>
              <button
                type="button"
                className="icon-button"
                aria-label="关闭通知"
                onClick={() => dismiss(item.id)}
              >
                <X size={15} />
              </button>
            </div>
          );
        })}
      </section>
    </ToastContext.Provider>
  );
}
