import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SeriesChart } from '../components/charts';
import { RecordTable } from '../components/workspace';
import { formatField, rankedRows } from '../lib/presentation';
import { groupContent } from '../lib/content';
describe('readable and accurate data presentation', () => {
  it('reranks the complete set when the selected metric changes, preserving missing and negative values', () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({
      title: `内容${i}`,
      views: 100 - i,
      shares: i,
    }));
    render(<SeriesChart rows={rows} x="title" fields={['views', 'shares']} />);
    expect(screen.queryByText('内容11')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('图表指标'), { target: { value: 'shares' } });
    expect(screen.getByText('内容11')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent('内容11');
    expect(
      rankedRows([{ value: null }, { value: -5 }, { value: 0 }, { value: 10 }], 'value', 10).map(
        (row) => row.value,
      ),
    ).toEqual([10, 0, -5]);
  });
  it('keeps identifiers intact, renders currency precision, and gives users control over detail columns', () => {
    render(
      <RecordTable
        rows={[
          {
            id: 12345,
            price: 12.5,
            sku: '很长的商品名称',
            platform: 'jd',
            quantity: 2,
            receiver: '示例',
            province: '示例省份',
          },
        ]}
        defaultColumns={['sku', 'price', 'platform']}
        caption="验收订单"
      />,
    );
    const table = screen.getByRole('table', { name: '验收订单' });
    expect(within(table).getByText('12.50').closest('td')).toHaveClass('number');
    expect(within(table).getByText('京东')).toBeInTheDocument();
    expect(within(table).queryByText('12345')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('显示列 3/7'));
    fireEvent.click(screen.getByRole('checkbox', { name: '编号' }));
    expect(within(table).getByText('12345')).toBeInTheDocument();
    expect(formatField('price', null)).toBe('—');
    expect(formatField('revenue', 0)).toBe('0.00');
  });
  it('orders weekday comparisons from Monday to Sunday instead of sorting Chinese characters', () => {
    expect(
      groupContent(
        [
          { weekday: '周日', views: 4 },
          { weekday: '周三', views: 2 },
          { weekday: '周二', views: 3 },
          { weekday: '周一', views: 1 },
        ],
        'weekday',
        ['views'],
      ).map((row) => row.weekday),
    ).toEqual(['周一', '周二', '周三', '周日']);
  });
});
