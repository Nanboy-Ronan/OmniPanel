import { numeric, text, type Row } from './resources';
export type ContentPlatform = 'wechat' | 'traffic' | 'xhs' | 'zhihu' | 'channels';
export const contentConfig: Record<
  ContentPlatform,
  {
    title: string;
    base: string;
    read: string;
    interactions: string[];
    accounts: string | null;
    fields: string[];
  }
> = {
  wechat: {
    title: '公众号内容分析',
    base: '/media',
    read: 'read_user_count',
    interactions: ['like_user', 'comment_count', 'collection_user', 'share_user_count'],
    accounts: '/media/accounts',
    fields: [
      'read_user_count',
      'share_user_count',
      'like_user',
      'comment_count',
      'collection_user',
    ],
  },
  traffic: {
    title: '公众号流量',
    base: '/media',
    read: 'read_user_count',
    interactions: ['like_user', 'comment_count', 'collection_user', 'share_user_count'],
    accounts: '/media/accounts',
    fields: ['read_user_count', 'read_count', 'share_user_count', 'like_user'],
  },
  xhs: {
    title: '小红书数据',
    base: '/media/xhs',
    read: 'views',
    interactions: ['likes', 'comments', 'collects', 'shares'],
    accounts: '/media/xhs/accounts',
    fields: ['impressions', 'views', 'likes', 'comments', 'collects', 'shares', 'new_followers'],
  },
  zhihu: {
    title: '知乎数据',
    base: '/media/zhihu',
    read: 'reads',
    interactions: ['likes', 'comments', 'collects', 'shares'],
    accounts: null,
    fields: ['reads', 'plays', 'likes', 'favorites', 'comments', 'collects', 'shares'],
  },
  channels: {
    title: '视频号数据',
    base: '/media/channels',
    read: 'plays',
    interactions: ['recommends', 'likes_thumb', 'comments', 'shares'],
    accounts: '/media/channels/accounts',
    fields: ['plays', 'recommends', 'likes_thumb', 'comments', 'shares', 'new_fans'],
  },
};
export function ratio(numerator: unknown, denominator: unknown) {
  const n = numeric(numerator),
    d = numeric(denominator);
  return n !== null && d !== null && d > 0 ? (n / d) * 100 : null;
}
export function normalizeContent(rows: Row[], platform: ContentPlatform): Row[] {
  const cfg = contentConfig[platform];
  return rows.map((row) => {
    const interactions = cfg.interactions.map((key) => numeric(row[key]));
    const total = interactions.some((n) => n !== null)
      ? interactions.reduce<number>((sum, n) => sum + (n ?? 0), 0)
      : null;
    const day = typeof row.publish_date === 'string' ? row.publish_date.slice(0, 10) : null;
    const date = day ? new Date(`${day}T00:00:00Z`) : null;
    return {
      ...row,
      ...Object.fromEntries(
        ['cover_click_rate', 'read_finish_rate', 'completion_rate']
          .filter((key) => key in row)
          .map((key) => [key, numeric(row[key]) === null ? null : Number(row[key]) * 100]),
      ),
      date: day,
      weekday:
        date && Number.isFinite(date.getTime())
          ? ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getUTCDay()]
          : '未知',
      total_engagement: total,
      follower_rate: ratio(row.new_followers ?? row.new_fans, row[cfg.read]),
      engagement_rate: ratio(total, row[cfg.read]),
      ...Object.fromEntries(
        cfg.interactions.map((key) => [`${key}_rate`, ratio(row[key], row[cfg.read])]),
      ),
    };
  });
}
export function groupContent(rows: Row[], key: string, fields: string[]): Row[] {
  const groups = new Map<string, Row>();
  for (const row of rows) {
    const value = text(row[key]);
    let group = groups.get(value);
    if (!group) {
      group = { [key]: value, posts: 0 };
      for (const field of fields) group[field] = null;
      groups.set(value, group);
    }
    group.posts = Number(group.posts) + 1;
    for (const field of fields) {
      const n = numeric(row[field]);
      if (n !== null) group[field] = (numeric(group[field]) ?? 0) + n;
    }
  }
  const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日', '未知'];
  return [...groups.values()].sort((a, b) =>
    key === 'weekday'
      ? weekdays.indexOf(text(a[key])) - weekdays.indexOf(text(b[key]))
      : text(a[key]).localeCompare(text(b[key])),
  );
}
export function matchTopics(title: string, topics: { name: string; keywords: string }[]) {
  const found = topics
    .filter(
      (topic) =>
        topic.name.trim() &&
        topic.keywords
          .split(/[,，]/)
          .some((word) => word.trim() && title.toLowerCase().includes(word.trim().toLowerCase())),
    )
    .map((topic) => topic.name);
  return found.length ? found : ['其他'];
}
