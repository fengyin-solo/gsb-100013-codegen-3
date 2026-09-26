<template>
  <section class="page" data-module="instrument">
    <header class="page-head">
      <div>
        <h2>仪器管理管理</h2>
        <p class="page-desc">维护检测仪器，围绕仪器编号、仪器名称、型号规格、所属实验室做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记检测仪器</button>
        <button class="btn" type="button" @click="exportRows">导出仪器管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>仪器编号</span>
        <input v-model="filters.keyword" placeholder="按仪器编号检索" />
      </label>
      <label class="filter-item">
        <span>仪器状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <span v-if="column === '仪器状态'" class="status-tag" :data-status="String(row.status ?? '')">
              {{ row[column] ?? '—' }}
            </span>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">查看</button>
            <button
              v-for="action in actionsFor(row)"
              :key="action"
              class="link"
              type="button"
              :disabled="actingId === row.id"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!actionsFor(row).length" class="muted">已终态</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无仪器管理数据，可先登记检测仪器</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条仪器管理记录</span>
      <span v-if="noticeMessage" class="ok-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="detail" class="modal-mask" @click.self="closeDetail">
      <div class="modal-card">
        <header class="modal-head">
          <h3>检测仪器 {{ detail['仪器编号'] ?? detail.id }}</h3>
          <button class="link" type="button" @click="closeDetail">关闭</button>
        </header>
        <dl class="detail-grid">
          <template v-for="column in columns" :key="column">
            <dt>{{ column }}</dt>
            <dd>{{ detail[column] ?? '—' }}</dd>
          </template>
        </dl>
        <h4>流转记录</h4>
        <ul v-if="detailHistory.length" class="history-list">
          <li v-for="(item, index) in detailHistory" :key="index">
            {{ item.at }}：{{ item.action }}（{{ item.from }} → {{ item.to }}）
          </li>
        </ul>
        <p v-else class="muted">暂无流转记录</p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/instrument'
const columns = ["仪器编号", "仪器名称", "型号规格", "所属实验室", "校准周期", "上次校准日", "下次校准日", "仪器状态"]
const statuses = ["在用", "待校准", "校准中", "已停用", "已报废"]

// 状态机与后端 ACTION_RULES 对齐：每个状态只暴露当前可执行的动作，终态不再给入口。
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  "在用": ["发起校准", "停用仪器"],
  "待校准": ["发起校准", "停用仪器"],
  "校准中": ["完成校准", "停用仪器"],
}

const rows = ref<Row[]>([])
const total = ref(0)
const summary = ref<Record<string, number>>({})
const errorMessage = ref('')
const noticeMessage = ref('')
const actingId = ref<number | null>(null)
const filters = ref<Record<string, string>>({ keyword: '', status: '' })
const detail = ref<Row | null>(null)

const stats = computed(() => [
  { label: '在用仪器', value: summary.value['在用'] ?? 0 },
  { label: '待校准仪器', value: summary.value['待校准'] ?? 0 },
  { label: '校准中仪器', value: summary.value['校准中'] ?? 0 },
  { label: '停用仪器', value: summary.value['已停用'] ?? 0 },
])

const detailHistory = computed(() => {
  const history = detail.value?.history
  return Array.isArray(history) ? history : []
})

function actionsFor(row: Row): string[] {
  return ACTIONS_BY_STATUS[String(row.status ?? '')] ?? []
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '检测仪器登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  if (actingId.value !== null) return
  actingId.value = Number(row.id)
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, expected_status: row.status } }),
    })
    const payload = await response.json().catch(() => null)
    const message = payload?.message ?? (response.ok ? '操作完成' : `接口返回 ${response.status}`)
    if (!response.ok || payload?.ok === false) {
      // 状态冲突（409）等失败：展示服务端说明，并刷新列表对齐最新状态。
      errorMessage.value = message
      await reload()
      return
    }
    noticeMessage.value = message
    await Promise.all([reload(), loadSummary()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '仪器管理操作失败'
  } finally {
    actingId.value = null
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error(`检测仪器 ${row.id} 读取失败（${response.status}）`)
    }
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测仪器明细读取失败'
  }
}

function closeDetail() {
  detail.value = null
}

async function reload() {
  const query = new URLSearchParams()
  if (filters.value.keyword) query.set('keyword', filters.value.keyword)
  if (filters.value.status) query.set('status', filters.value.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('检测仪器列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '仪器管理列表读取失败'
  }
}

async function loadSummary() {
  try {
    const response = await request(`${ENDPOINT}/summary`)
    if (!response.ok) return
    const payload = await response.json()
    summary.value = payload.summary ?? {}
  } catch {
    // 汇总卡片失败不阻塞列表
  }
}

onMounted(() => {
  void reload()
  void loadSummary()
})
</script>

<style scoped>
.status-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: #eef2f7;
  color: #475569;
}
.status-tag[data-status='在用'] { background: #e7f6ec; color: #157347; }
.status-tag[data-status='待校准'] { background: #fff4e0; color: #b45309; }
.status-tag[data-status='校准中'] { background: #e8f0fe; color: #1f6feb; }
.status-tag[data-status='已停用'],
.status-tag[data-status='已报废'] { background: #f1f2f4; color: #6b7280; }
.muted { color: var(--muted); font-size: 12px; }
.ok-text { color: #157347; }
.link:disabled { color: var(--muted); cursor: not-allowed; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
}
.modal-card {
  background: #fff;
  border-radius: 10px;
  padding: 16px 20px;
  width: 520px;
  max-height: 80vh;
  overflow: auto;
}
.modal-head { display: flex; justify-content: space-between; align-items: center; }
.modal-head h3 { margin: 0; font-size: 15px; }
.detail-grid {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 6px 12px;
  margin: 12px 0;
  font-size: 13px;
}
.detail-grid dt { color: var(--muted); }
.detail-grid dd { margin: 0; }
.history-list { margin: 0; padding-left: 18px; font-size: 13px; }
h4 { margin: 8px 0; font-size: 13px; }
select {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 5px 8px;
  background: #fff;
}
</style>
