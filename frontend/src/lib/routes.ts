import type { User } from './api';
export const routes = [
  ['overview', '经营概览', '商城分析', 'analyst', 'KPI 看板'],
  ['analysis', '数据分析', '商城分析', 'analyst', '数据分析'],
  ['customers', '客户管理', '商城分析', 'analyst', '客户管理'],
  ['identity', '跨平台客户', '商城分析', 'analyst', '跨平台客户'],
  ['retention', '客户留存', '商城分析', 'analyst', '客户留存'],
  ['orders', '订单明细', '数据管理', 'viewer', '数据浏览'],
  ['upload', '数据上传', '数据管理', 'viewer', '数据上传'],
  ['tasks', '导入任务', '数据管理', 'viewer', '导入任务'],
  ['dictionary', '数据字典', '数据管理', 'viewer', '数据字典'],
  ['sql', 'SQL 控制台', '数据管理', 'analyst', 'SQL 控制台'],
  ['reports', '周报', '内容分析', 'analyst', '周报'],
  ['traffic', '公众号流量', '内容分析', 'analyst', '公众号流量'],
  ['wechat', '公众号内容', '内容分析', 'analyst', '公众号内容分析'],
  ['impact', '内容带货分析', '内容分析', 'analyst', '内容带货分析'],
  ['xhs', '小红书数据', '内容分析', 'analyst', '小红书数据'],
  ['pgy', '蒲公英合作', '内容分析', 'analyst', '蒲公英合作'],
  ['zhihu', '知乎数据', '内容分析', 'analyst', '知乎数据'],
  ['channels', '视频号数据', '内容分析', 'analyst', '视频号数据'],
  ['users', '用户管理', '系统管理', 'admin', '用户管理'],
  ['logs', '操作日志', '系统管理', 'admin', '操作日志'],
  ['database', '数据库状态', '系统管理', 'admin', '数据库状态'],
  ['collector', '自动采集', '系统管理', 'admin', '自动采集'],
].map(([key, title, group, role, alias]) => ({ key, title, group, role, alias }));
export function allowed(role: User['role'], minimum: string) {
  return (
    (role === 'admin' ? 2 : role === 'analyst' ? 1 : 0) >=
    (minimum === 'admin' ? 2 : minimum === 'analyst' ? 1 : 0)
  );
}
export function resolveRoute(page: string) {
  return routes.find((route) => route.key === page || route.alias === page);
}
