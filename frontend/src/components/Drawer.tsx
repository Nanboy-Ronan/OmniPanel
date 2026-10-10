import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

/** Right-side modal panel for record details; Esc, the close button and the backdrop dismiss it. */
export function Drawer({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current!;
    if (!el.open) el.showModal();
    return () => el.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="drawer"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <header className="drawer-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <button className="icon-button" aria-label="关闭详情" title="关闭（Esc）" onClick={onClose}>
          <X size={18} />
        </button>
      </header>
      <div className="drawer-body">{children}</div>
    </dialog>
  );
}
