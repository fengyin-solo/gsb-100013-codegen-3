<template>
  <section class="page" data-module="instrument">
    <header class="page-head">
      <div>
        <h2>检测仪器档案</h2>
        <p class="page-desc">维护检测仪器与仪器编号，按“在用 → 待校准 → 校准中 → 在用”管理校准流程，停用档案保留可查。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记检测仪器</button>
        <button class="btn" type="button" @click="exportRows">导出仪器档案清单</button>
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
        <input v-model="keyword" placeholder="按仪器编号检索" />
      </label>
      <label class="filter-item">
        <span>档案状态</span>
        <select v-model="statusFilter">
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
          <th>档案</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <span v-if="column === '仪器状态'" class="status-badge" :class="statusClass(String(row.status))">
              {{ displayStatus(row) }}
            </span>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <template v-if="availableActions(row).length">
              <button
                v-for="action in availableActions(row)"
                :key="action"
                class="link"
                type="button"
                :disabled="isRowBusy(row)"
                :title="actionHint(String(row.status), action)"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <span v-else class="muted-text">终态档案，仅可查看</span>
          </td>
          <td>
            <button class="link" type="button" @click="openDetail(row)">查看档案</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无符合条件的检测仪器档案</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条检测仪器档案</span>
      <span v-if="successMessage" class="success-text">{{ successMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <teleport to="body">
      <div v-if="detailRow" class="modal-mask" role="presentation" @click.self="closeDetail">
        <section class="modal" role="dialog" aria-modal="true" aria-labelledby="instrument-detail-title">
          <header class="modal-head">
            <div>
              <h3 id="instrument-detail-title">检测仪器档案</h3>
              <p>{{ detailRow['仪器编号'] }} · 当前版本 v{{ detailRow.version ?? 1 }}</p>
            </div>
            <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
          </header>

          <div class="detail-status">
            <span class="status-badge" :class="statusClass(String(detailRow.status))">{{ displayStatus(detailRow) }}</span>
            <span v-if="!availableActions(detailRow).length" class="muted-text">已到终态，不能再执行状态动作</span>
          </div>

          <dl class="detail-grid">
            <template v-for="column in detailFields" :key="column">
              <dt>{{ column }}</dt>
              <dd v-if="column === '仪器状态'">{{ displayStatus(detailRow) }}</dd>
              <dd v-else>{{ detailRow[column] ?? '—' }}</dd>
            </template>
          </dl>

          <h4>状态流转记录</h4>
          <ol class="history-list">
            <li v-for="item in historyRows(detailRow)" :key="`${item.seq}-${item.action}`">
              <span class="history-step">v{{ item.version }}</span>
              <span>{{ item.from_status ?? '建档' }} → {{ item.to_status }}</span>
              <strong>{{ item.action }}</strong>
              <time>{{ item.at ?? '既有记录时间未记录' }}</time>
            </li>
          </ol>
        </section>
      </div>
    </teleport>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Status = '在用' | '待校准' | '校准中' | '已停用'
type ActionName = '发起校准' | '完成校准' | '停用仪器'
type HistoryItem = {
  seq: number
  version: number
  action: string
  from_status: string | null
  to_status: string
  remark?: string | null
  at?: string | null
}

type Row = {
  id: number
  status: string
  version: number
  仪器状态?: string | null
  available_actions?: string[]
  history?: HistoryItem[]
  [field: string]: string | number | null | string[] | HistoryItem[] | undefined
}

const ENDPOINT = '/api/instrument'
const columns = ['仪器编号', '仪器名称', '型号规格', '所属实验室', '校准周期', '上次校准日', '下次校准日', '仪器状态']
const detailFields = columns
const statuses: Status[] = ['在用', '待校准', '校准中', '已停用']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const detailRow = ref<Row | null>(null)
const pendingActions = ref<Record<string, boolean>>({})

const stats = computed(() => {
  const countByStatus = (status: Status) => rows.value.filter(row => String(row.status) === status).length
  return [
    { label: '在用仪器', value: countByStatus('在用') },
    { label: '待校准仪器', value: countByStatus('待校准') },
    { label: '校准中仪器', value: countByStatus('校准中') },
    { label: '停用仪器', value: countByStatus('已停用') },
  ]
})

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '检测仪器登记入口尚未接入审批流'
  successMessage.value = ''
}

function actionKey(row: Row, action: string) {
  return `${row.id}:${action}`
}

function isRowBusy(row: Row) {
  return Object.keys(pendingActions.value).some(key => key.startsWith(`${row.id}:`))
}

function availableActions(row: Row): ActionName[] {
  const fallback: Record<string, ActionName[]> = {
    在用: ['发起校准', '停用仪器'],
    待校准: ['发起校准', '停用仪器'],
    校准中: ['完成校准', '停用仪器'],
    已停用: [],
  }
  return (row.available_actions as ActionName[] | undefined) ?? fallback[String(row.status)] ?? []
}

function displayStatus(row: Row) {
  const status = String(row.status ?? '')
  return statuses.includes(status as Status) ? status : (String(row['仪器状态'] || '') || '未知状态')
}

function statusClass(status: string) {
  return {
    'status-in-use': status === '在用',
    'status-pending': status === '待校准',
    'status-calibrating': status === '校准中',
    'status-disabled': status === '已停用',
    'status-legacy': !statuses.includes(status as Status),
  }
}

function actionHint(status: string, action: ActionName) {
  if (action === '发起校准') {
    return status === '待校准' ? '将状态从待校准切换为校准中' : '将状态从在用切换为待校准'
  }
  if (action === '完成校准') return '校准完成后恢复为在用'
  return '停用后档案仅保留查看，不能再执行状态动作'
}

function historyRows(row: Row): HistoryItem[] {
  return Array.isArray(row.history) ? row.history as HistoryItem[] : []
}

async function readPayload(response: Response) {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function extractError(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object') {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string') {
      return (detail as { message: string }).message
    }
    if (typeof (payload as { message?: unknown }).message === 'string') {
      return (payload as { message: string }).message
    }
  }
  return fallback
}

async function runAction(action: ActionName, row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  const key = actionKey(row, action)
  if (pendingActions.value[key]) {
    errorMessage.value = '该操作正在提交，请勿重复点击'
    return
  }

  pendingActions.value[key] = true
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        action,
        expected_version: row.version ?? 1,
      }),
    })
    const payload = await readPayload(response)
    if (!response.ok || payload?.ok === false) {
      if (payload?.code === 'STATE_CONFLICT' || payload?.code === 'VERSION_CONFLICT') await reload()
      throw new Error(extractError(payload, '状态已变化或操作冲突，请刷新后重试'))
    }

    await reload()
    successMessage.value = payload?.message ?? '状态已更新'
    closeDetailIfSame(row.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '仪器档案操作失败'
  } finally {
    delete pendingActions.value[key]
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (statusFilter.value) query.set('status', statusFilter.value)

  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    const payload = await readPayload(response)
    if (!response.ok) {
      throw new Error(extractError(payload, '检测仪器档案读取失败'))
    }
    rows.value = Array.isArray(payload?.items) ? payload.items as Row[] : []
    total.value = Number(payload?.total ?? rows.value.length)
    refreshDetail()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测仪器档案读取失败'
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    const payload = await readPayload(response)
    if (!response.ok) {
      throw new Error(extractError(payload, '检测仪器档案读取失败'))
    }
    detailRow.value = payload as Row
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测仪器档案读取失败'
  }
}

function refreshDetail() {
  if (!detailRow.value) return
  const latest = rows.value.find(row => row.id === detailRow.value?.id)
  if (latest) {
    detailRow.value = latest
  }
}

function closeDetailIfSame(id: number) {
  if (detailRow.value?.id === id) {
    detailRow.value = rows.value.find(row => row.id === id) ?? null
  }
}

function closeDetail() {
  detailRow.value = null
}

function handlePageShow(event: PageTransitionEvent) {
  if (event.persisted) void reload()
}

onMounted(() => {
  void reload()
  window.addEventListener('pageshow', handlePageShow)
})

onBeforeUnmount(() => {
  window.removeEventListener('pageshow', handlePageShow)
})
</script>
