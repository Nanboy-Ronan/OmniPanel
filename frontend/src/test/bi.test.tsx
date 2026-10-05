import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { compact, contentQuadrants, publicationCalendar, median, dashboardSchema } from '../lib/bi';
import ContentExplorer from '../components/ContentExplorer';
import fixtures from './workspace-fixtures.json';
const rows = [
  {
    id: 1,
    title: '高传播高互动',
    date: '2026-10-01',
    publish_date: '2026-10-01',
    views: 100,
    engagement_rate: 10,
    total_engagement: 10,
    likes: 10,
  },
  {
    id: 2,
    title: '低传播高互动',
    date: '2026-10-02',
    publish_date: '2026-10-02',
    views: 10,
    engagement_rate: 10,
    total_engagement: 1,
    likes: 1,
  },
  {
    id: 3,
    title: '高传播低互动',
    date: '2026-10-03',
    publish_date: '2026-10-03',
    views: 100,
    engagement_rate: 1,
    total_engagement: 1,
    likes: 1,
  },
  {
    id: 4,
    title: '低传播低互动',
    date: '2026-10-03',
    publish_date: '2026-10-03',
    views: 10,
    engagement_rate: 1,
    total_engagement: 0,
    likes: 0,
  },
];
describe('BI exploration semantics', () => {
  it('preserves small nonzero amounts in compact chart labels', () => {
    expect(compact(0.03)).toBe('0.03');
    expect(compact(-0.03)).toBe('-0.03');
    expect(compact(40948.36)).toContain('万');
  });
  it('excludes unavailable and zero reach, uses full-sample medians, and preserves zero interaction', () => {
    const result = contentQuadrants(
      [
        ...rows,
        { id: 5, views: 0, engagement_rate: null },
        { id: 6, views: null, engagement_rate: null },
      ],
      'views',
    );
    expect(result.rows.map((r) => r.quadrant)).toEqual([0, 1, 2, 3]);
    expect(result.x).toBe(55);
    expect(result.y).toBe(5.5);
    expect(median([])).toBe(0);
    expect(publicationCalendar(rows, '2026-10-03', 3).map((d) => d.count)).toEqual([1, 1, 2]);
  });
  it('links quadrant and calendar selections to the actual content table and resets them', () => {
    const onDetail = vi.fn();
    render(<ContentExplorer rows={rows} platform="xhs" end="2026-10-03" onDetail={onDetail} />);
    const table = () => screen.getByRole('table', { name: '图表联动内容明细' });
    expect(within(table()).getAllByRole('row')).toHaveLength(5);
    fireEvent.click(screen.getByRole('button', { name: /高传播 · 高互动/ }));
    expect(within(table()).getAllByRole('row')).toHaveLength(2);
    expect(within(table()).getByText('高传播高互动')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '清除图表筛选 ×' }));
    fireEvent.click(screen.getByRole('button', { name: '2026-10-03：2 篇' }));
    expect(within(table()).getAllByRole('row')).toHaveLength(3);
    fireEvent.click(within(table()).getAllByRole('button', { name: '查看' })[0]);
    expect(onDetail).toHaveBeenCalledWith(expect.objectContaining({ id: 3 }));
  });
  it('spreads long-tail content on a log axis by default and permits switching back', () => {
    render(
      <ContentExplorer
        rows={[...rows, { ...rows[0], id: 7, views: 100000 }]}
        platform="xhs"
        end="2026-10-03"
        onDetail={vi.fn()}
      />,
    );
    const toggle = screen.getByRole('checkbox', { name: '对数横轴' });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    expect(toggle).not.toBeChecked();
  });
  it('rejects incomplete dashboard payloads and validates comparison dates in fixtures', () => {
    expect(dashboardSchema.safeParse({}).success).toBe(false);
    const d = dashboardSchema.parse(fixtures['/analysis/dashboard']);
    expect(d.series.reduce((sum, r) => sum + r.revenue, 0)).toBeCloseTo(d.current.revenue, 2);
  });
});
