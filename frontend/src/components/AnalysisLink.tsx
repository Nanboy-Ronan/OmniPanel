import { useState } from 'react';
export function AnalysisLink({ filters = {} }: { filters?: Record<string, string> }) {
  const [message, setMessage] = useState('');
  return (
    <span className="analysis-link">
      <button
        onClick={async () => {
          try {
            const url = new URL(window.location.href);
            for (const [key, value] of Object.entries(filters)) url.searchParams.set(key, value);
            await navigator.clipboard.writeText(url.toString());
            setMessage('链接已复制；访问者仍需登录并拥有相应权限。');
          } catch {
            setMessage('可复制浏览器地址栏分享当前分析。');
          }
        }}
      >
        复制分析链接
      </button>
      {message && <small role="status">{message}</small>}
    </span>
  );
}
