import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import Dashboard from './pages/Dashboard.vue'
import Catalog from './pages/Catalog.vue'
import Delivery from './pages/Delivery.vue'
import Credit from './pages/Credit.vue'
import Value from './pages/Value.vue'
import Compare from './pages/Compare.vue'
import Audit from './pages/Audit.vue'
import DatasetTrust from './pages/DatasetTrust.vue'
import DatasetAudit from './pages/DatasetAudit.vue'
import Workbench from './pages/Workbench.vue'
import TaskMeasurement from './pages/TaskMeasurement.vue'
import VisualCases from './pages/VisualCases.vue'
import SameAssetJoint from './pages/SameAssetJoint.vue'
import './style.css'

const router = createRouter({ history: createWebHashHistory(), routes: [
  { path: '/same-asset-joint', component: SameAssetJoint, meta: { title: '同版本可信—价值联合评估', subtitle: '核验资产、证据、任务和评价协议，让两类结论针对同一个精确数据版本' } },
  { path: '/visual-cases', component: VisualCases, meta: { title: '新数据集视觉证据', subtitle: '注册精确数据版本与真实图像，按明确规则执行并追溯AI视觉审阅' } },
  { path: '/task-measurement', component: TaskMeasurement, meta: { title: '新CSV任务测量', subtitle: '上传独立版本，固定买方、任务与协议，运行条件边际效用测量' } },
  { path: '/workbench', component: Workbench, meta: { title: '数据集评估工作台', subtitle: '固定版本、证据预算、条件任务价值与使用边界' } },
  { path: '/', component: Dashboard, meta: { title: '系统总览', subtitle: '从数据登记到研究决策，追踪每一步真实证据' } },
  { path: '/assets', component: Catalog, meta: { title: '数据资产目录', subtitle: '查看版本、数据来源与经过验证的测量' } },
  { path: '/delivery', component: Delivery, meta: { title: '授权与交付', subtitle: '通过独立节点执行真实 HTTP 试交付' } },
  { path: '/credit', component: Credit, meta: { title: '动态信用评估', subtitle: '基于可验证合同观测的履约概率研究' } },
  { path: '/value', component: Value, meta: { title: '场景化价值评估', subtitle: '将任务收益、履约信用与成本放入同一决策情境' } },
  { path: '/compare', component: Compare, meta: { title: '资产价值对比', subtitle: '保留负贡献与不购买选项，解释研究建议的边界' } },
  { path: '/audit', component: Audit, meta: { title: '审计与研究报告', subtitle: '回溯事件，查看并导出完整评估依据' } },
  { path: '/dataset-trust', component: DatasetTrust, meta: { title: '数据集可信评估', subtitle: '测量结构，核查证据，保留未知，与任务价值分别解释' } },
  { path: '/dataset-audit', component: DatasetAudit, meta: { title: '风险约束可信审计', subtitle: '先固定审计预算与容忍率，用可追溯证据判断条件接受、拒绝或继续取证' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
] })
createApp(App).use(router).mount('#app')

