import type { Dashboard } from '../lib/bi';
import { platformNames } from '../lib/labels';
import { formatField } from '../lib/presentation';
export default function DecisionBrief({
  data,
  onPlatform,
}: {
  data: Dashboard;
  onPlatform?: (value: string) => void;
}) {
  const changed = data.channels.map((r) => ({
    platform: r.platform,
    change: r.current.revenue - r.prior.revenue,
  }));
  const decline = [...changed].filter((r) => r.change < 0).sort((a, b) => a.change - b.change)[0];
  const growth = [...changed].filter((r) => r.change > 0).sort((a, b) => b.change - a.change)[0];
  const diff = data.current.revenue - data.prior.revenue;
  return (
    <section className="decision-brief" aria-label="经营变化与分析入口">
      <article>
        <span>
          经营变化 · {data.start_date} — {data.end_date}
        </span>
        <h3>
          {!data.current.orders
            ? '当前区间没有订单'
            : diff === 0
              ? '营业额与前期持平'
              : `营业额较前期${diff > 0 ? '增加' : '减少'}`}
        </h3>
        <strong>
          {diff === 0 ? '—' : `${diff > 0 ? '+' : '−'}¥${formatField('price', Math.abs(diff))}`}
        </strong>
        <p>
          对照 {data.prior_start} — {data.prior_end}，按相同天数比较。
        </p>
      </article>
      <article className={decline ? 'needs-attention' : ''}>
        <span>{decline ? '优先追查 · 下降贡献最大' : '增长来源 · 增长贡献最大'}</span>
        <h3>
          {decline || growth
            ? platformNames[(decline || growth)!.platform] || (decline || growth)!.platform
            : '暂无渠道变化'}
        </h3>
        <strong>
          {decline || growth
            ? `${decline ? '−' : '+'}¥${formatField('price', Math.abs((decline || growth)!.change))}`
            : '—'}
        </strong>
        <p>
          {decline ? '建议检查渠道趋势、商品与订单构成。' : '可以进一步查看贡献渠道的商品结构。'}
        </p>
        {(decline || growth) && onPlatform && (
          <button onClick={() => onPlatform((decline || growth)!.platform)}>分析该渠道 →</button>
        )}
      </article>
      <article>
        <span>数据可解释性</span>
        <h3>
          {data.current.missing_amount
            ? `${data.current.missing_amount} 条订单缺少金额`
            : '订单金额字段完整'}
        </h3>
        <p>
          营业额来自已知金额；客单价仅使用 {data.current.priced_orders}{' '}
          条金额非空记录。客户数按区间去重。
        </p>
        <small>变化描述已导入记录，不代表实时交易或变化原因。</small>
      </article>
    </section>
  );
}
