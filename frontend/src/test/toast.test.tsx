import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ToastProvider, useToast } from '../components/Toast';

function Trigger() {
  const toast = useToast();
  return (
    <>
      <button onClick={() => toast.success('视图已保存')}>成功</button>
      <button onClick={() => toast.error('保存失败：网络中断')}>失败</button>
    </>
  );
}

describe('toast notifications', () => {
  it('announces politely, stacks by tone, and auto-dismisses', () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    const region = screen.getByRole('region', { name: '通知' });
    expect(region).toHaveAttribute('aria-live', 'polite');
    fireEvent.click(screen.getByRole('button', { name: '成功' }));
    fireEvent.click(screen.getByRole('button', { name: '失败' }));
    expect(screen.getByText('视图已保存').closest('.toast')).toHaveClass('toast-success');
    expect(screen.getByText('保存失败：网络中断').closest('.toast')).toHaveClass('toast-error');
    // Notices never use role="alert"; inline ErrorState stays the assertive channel.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(4100));
    expect(screen.queryByText('视图已保存')).not.toBeInTheDocument();
    expect(screen.getByText('保存失败：网络中断')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(2000));
    expect(screen.queryByText('保存失败：网络中断')).not.toBeInTheDocument();
  });
  it('closes on demand and keeps at most four notices', () => {
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    for (let i = 0; i < 6; i++) fireEvent.click(screen.getByRole('button', { name: '成功' }));
    expect(screen.getAllByText('视图已保存')).toHaveLength(4);
    fireEvent.click(screen.getAllByRole('button', { name: '关闭通知' })[0]);
    expect(screen.getAllByText('视图已保存')).toHaveLength(3);
  });
  it('is a silent no-op outside a provider', () => {
    render(<Trigger />);
    fireEvent.click(screen.getByRole('button', { name: '成功' }));
    expect(screen.queryByText('视图已保存')).not.toBeInTheDocument();
  });
});
