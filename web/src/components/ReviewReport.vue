<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Check,
  FilePlus2,
  Pencil,
  RefreshCcw,
  Save,
  Trash2,
  X,
} from 'lucide-vue-next'
import type {
  MemoryProposal,
  ReportFreshness,
  ReviewAnnotation,
  ReviewReportDetail,
  ReviewReportSummary,
} from '../lib/reviews'

const props = withDefaults(defineProps<{
  detail: ReviewReportDetail | null
  reports: ReviewReportSummary[]
  freshness: ReportFreshness
  pending: boolean
  readonly?: boolean
}>(), { readonly: false })

const emit = defineEmits<{
  version: [version: number]
  regenerate: [instruction: string]
  'add-annotation': [content: string]
  'update-annotation': [annotation: ReviewAnnotation, content: string]
  'delete-annotation': [annotation: ReviewAnnotation]
  confirm: [proposal: MemoryProposal]
  reject: [proposal: MemoryProposal]
}>()

const annotationDraft = ref('')
const instruction = ref('')
const editingId = ref('')
const editingContent = ref('')

const selectedVersion = computed(() => props.detail?.report.version ?? null)
const freshnessText = computed(() => ({
  none: '',
  current: '基于当前记录',
  stale_user_input: '补充事实已有变化',
  stale_business_facts: '当天记录已有变化',
  stale_both: '补充事实和当天记录已有变化',
}[props.freshness]))

watch(selectedVersion, () => {
  editingId.value = ''
  editingContent.value = ''
  annotationDraft.value = ''
})
watch(
  () => props.detail?.annotations.map(item => `${item.id}:${item.version}`).join(','),
  () => {
    annotationDraft.value = ''
    editingId.value = ''
    editingContent.value = ''
  },
)

function startEdit(annotation: ReviewAnnotation) {
  editingId.value = annotation.id
  editingContent.value = annotation.content
}
</script>

<template>
  <section class="review-report" aria-labelledby="review-report-title">
    <header class="review-section-heading review-report-heading">
      <div>
        <p class="review-eyebrow">版本化结果</p>
        <h2 id="review-report-title">复盘报告</h2>
      </div>
      <label v-if="reports.length" class="review-version-picker">
        <span>报告版本</span>
        <select
          :value="selectedVersion ?? reports[0]?.version"
          :disabled="pending"
          @change="emit('version', Number(($event.target as HTMLSelectElement).value))"
        >
          <option v-for="report in reports" :key="report.id" :value="report.version">
            第 {{ report.version }} 版
          </option>
        </select>
      </label>
    </header>

    <div v-if="detail" class="review-report-body">
      <div
        v-if="freshness !== 'current' && freshness !== 'none'"
        class="review-stale"
        role="status"
      >
        <strong>{{ freshnessText }}</strong>
        <span>当前版本保持不变，可按最新信息重新生成。</span>
      </div>

      <section class="review-report-lead">
        <span>第 {{ detail.report.version }} 版</span>
        <p>{{ detail.report.summary }}</p>
      </section>

      <div class="review-report-sections">
        <section>
          <h3>已确认事实</h3>
          <ul v-if="detail.report.facts.length">
            <li v-for="item in detail.report.facts" :key="`${item.source_key}:${item.text}`">
              {{ item.text }}
            </li>
          </ul>
          <p v-else class="review-empty">没有足够的已记录事实。</p>
        </section>
        <section>
          <h3>你的说明</h3>
          <ul v-if="detail.report.user_statements.length">
            <li v-for="item in detail.report.user_statements" :key="item">{{ item }}</li>
          </ul>
          <p v-else class="review-empty">没有额外说明。</p>
        </section>
        <section>
          <h3>观察与推测</h3>
          <ul v-if="detail.report.insights.length">
            <li v-for="item in detail.report.insights" :key="item.text">
              {{ item.text }} <span class="review-source-label">AI 推测</span>
            </li>
          </ul>
          <p v-else class="review-empty">没有需要补充的推测。</p>
        </section>
        <section>
          <h3>下一步</h3>
          <ul v-if="detail.report.next_actions.length">
            <li v-for="item in detail.report.next_actions" :key="item">{{ item }}</li>
          </ul>
          <p v-else class="review-empty">暂时没有下一步建议。</p>
        </section>
        <section v-if="detail.report.uncertainties.length">
          <h3>信息缺口</h3>
          <ul>
            <li v-for="item in detail.report.uncertainties" :key="item">{{ item }}</li>
          </ul>
        </section>
      </div>

      <section class="review-memory-proposals" aria-labelledby="memory-proposal-title">
        <h3 id="memory-proposal-title">记忆建议</h3>
        <p v-if="!detail.memory_proposals.length" class="review-empty">
          这一版没有长期记忆建议。
        </p>
        <ul v-else>
          <li v-for="proposal in detail.memory_proposals" :key="proposal.id">
            <div>
              <span class="review-source-label">{{ proposal.kind }}</span>
              <strong>{{ proposal.title }}</strong>
              <p>{{ proposal.content }}</p>
              <small>{{ proposal.rationale }}</small>
            </div>
            <div v-if="proposal.status === 'pending'" class="review-inline-actions">
              <button
                type="button"
                class="icon-button"
                title="确认保存"
                aria-label="确认保存记忆"
                :disabled="pending"
                @click="emit('confirm', proposal)"
              >
                <Check :size="17" aria-hidden="true" />
              </button>
              <button
                type="button"
                class="icon-button"
                title="拒绝"
                aria-label="拒绝记忆建议"
                :disabled="pending"
                @click="emit('reject', proposal)"
              >
                <X :size="17" aria-hidden="true" />
              </button>
            </div>
            <span v-else class="review-proposal-state">
              {{ proposal.status === 'confirmed' ? '已保存' : '已拒绝' }}
            </span>
          </li>
        </ul>
      </section>

      <section class="review-annotations" aria-labelledby="review-annotations-title">
        <h3 id="review-annotations-title">批注</h3>
        <ul v-if="detail.annotations.length">
          <li v-for="annotation in detail.annotations" :key="annotation.id">
            <template v-if="editingId === annotation.id">
              <textarea v-model="editingContent" rows="3" maxlength="4000" />
              <div class="review-inline-actions">
                <button
                  type="button"
                  class="icon-button"
                  title="保存批注"
                  aria-label="保存批注"
                  :disabled="pending || !editingContent.trim()"
                  @click="emit('update-annotation', annotation, editingContent.trim())"
                >
                  <Save :size="17" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="icon-button"
                  title="取消编辑"
                  aria-label="取消编辑批注"
                  @click="editingId = ''"
                >
                  <X :size="17" aria-hidden="true" />
                </button>
              </div>
            </template>
            <template v-else>
              <p>{{ annotation.content }}</p>
              <div class="review-inline-actions">
                <button
                  type="button"
                  class="icon-button"
                  title="编辑批注"
                  aria-label="编辑批注"
                  :disabled="pending"
                  @click="startEdit(annotation)"
                >
                  <Pencil :size="16" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="icon-button danger"
                  title="删除批注"
                  aria-label="删除批注"
                  :disabled="pending"
                  @click="emit('delete-annotation', annotation)"
                >
                  <Trash2 :size="16" aria-hidden="true" />
                </button>
              </div>
            </template>
          </li>
        </ul>
        <form class="review-annotation-form" @submit.prevent="emit('add-annotation', annotationDraft.trim())">
          <textarea
            v-model="annotationDraft"
            rows="2"
            maxlength="4000"
            placeholder="为这个版本补充批注"
            aria-label="新批注"
          />
          <button
            type="submit"
            class="button secondary"
            :disabled="pending || !annotationDraft.trim()"
          >
            <FilePlus2 :size="17" aria-hidden="true" />
            添加批注
          </button>
        </form>
      </section>

      <form v-if="!readonly" class="review-regenerate" @submit.prevent="emit('regenerate', instruction.trim())">
        <label for="review-regenerate-instruction">重新生成要求 <span>可选</span></label>
        <textarea
          id="review-regenerate-instruction"
          v-model="instruction"
          rows="2"
          maxlength="2000"
          placeholder="例如：更关注计划与实际执行的差异"
        />
        <button type="submit" class="button secondary" :disabled="pending">
          <RefreshCcw :size="17" aria-hidden="true" />
          生成新版本
        </button>
      </form>
    </div>

    <div v-else class="review-report-empty">
      <FilePlus2 :size="24" aria-hidden="true" />
      <p>完成事实补充后，可直接生成第一版报告。</p>
    </div>
  </section>
</template>
