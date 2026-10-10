import { useEffect, useMemo, useRef, useState } from 'react';
import { CornerDownLeft, Search } from 'lucide-react';
import { navIcon } from '../lib/navIcons';

type Item = { key: string; title: string; group: string; alias: string };

/** Keyboard page switcher (⌘K / Ctrl+K). Items are pre-filtered to what the user may open. */
export function CommandPalette({
  items,
  current,
  onGo,
  onClose,
}: {
  items: Item[];
  current: string;
  onGo: (key: string) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  useEffect(() => {
    const el = dialog.current!;
    if (!el.open) el.showModal();
    return () => el.close();
  }, []);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) =>
      [item.title, item.alias, item.group, item.key].some((text) => text.toLowerCase().includes(q)),
    );
  }, [items, query]);
  useEffect(() => setActive(0), [query]);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [active]);
  const groups = [...new Set(matches.map((item) => item.group))];
  return (
    <dialog
      ref={dialog}
      className="palette"
      aria-label="快速跳转"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="palette-search">
        <Search size={18} />
        <input
          autoFocus
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={matches[active] ? `palette-${matches[active].key}` : undefined}
          aria-label="搜索页面"
          placeholder="搜索页面，例如：订单、留存、小红书"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              setActive((value) => Math.min(value + 1, matches.length - 1));
            } else if (event.key === 'ArrowUp') {
              event.preventDefault();
              setActive((value) => Math.max(value - 1, 0));
            } else if (event.key === 'Enter' && matches[active]) {
              event.preventDefault();
              onGo(matches[active].key);
            }
          }}
        />
      </div>
      {matches.length ? (
        <ul className="palette-list" id="palette-list" role="listbox" ref={list}>
          {groups.map((group) => (
            <li key={group} role="presentation">
              <div className="palette-group">{group}</div>
              {matches.map((item, index) => {
                if (item.group !== group) return null;
                const Icon = navIcon(item.key);
                return (
                  <button
                    key={item.key}
                    id={`palette-${item.key}`}
                    role="option"
                    aria-selected={index === active}
                    data-index={index}
                    className="palette-item"
                    tabIndex={-1}
                    onMouseMove={() => setActive(index)}
                    onClick={() => onGo(item.key)}
                  >
                    <Icon size={16} aria-hidden />
                    {item.title}
                    {item.key === current && <small>当前页面</small>}
                  </button>
                );
              })}
            </li>
          ))}
        </ul>
      ) : (
        <p className="palette-empty">没有匹配“{query}”的页面</p>
      )}
      <footer className="palette-foot">
        <span>
          <kbd>↑</kbd>
          <kbd>↓</kbd>选择
        </span>
        <span>
          <kbd>
            <CornerDownLeft size={10} />
          </kbd>
          打开
        </span>
        <span>
          <kbd>Esc</kbd>关闭
        </span>
      </footer>
    </dialog>
  );
}
