<script setup lang="ts">
import {
  CheckSquare2,
  Folder,
  Inbox,
  LogOut,
  Pencil,
  Plus,
  Target,
} from 'lucide-vue-next'
import type { Project, ProjectKind } from '../lib/tasks'
import ProductNavigation from './ProductNavigation.vue'

defineProps<{
  projects: Project[]
  selectedProjectId: string | null
  inbox: boolean
  email: string
  pendingLogout: boolean
}>()

const emit = defineEmits<{
  location: [projectId: string | null, inbox: boolean]
  createProject: [kind: ProjectKind]
  editProject: [project: Project]
  logout: []
}>()
</script>

<template>
  <aside class="task-navigation" aria-label="iTime 导航">
    <div class="task-navigation-top">
      <RouterLink to="/tasks" class="wordmark" aria-label="iTime 任务">iTime<span aria-hidden="true">.</span></RouterLink>
      <ProductNavigation current="tasks" />
    </div>

    <div class="task-locations">
      <p class="nav-heading">任务视图</p>
      <button
        type="button"
        class="nav-link"
        :class="{ selected: !selectedProjectId && !inbox }"
        :aria-pressed="!selectedProjectId && !inbox"
        @click="emit('location', null, false)"
      >
        <CheckSquare2 :size="18" aria-hidden="true" />所有任务
      </button>
      <button
        type="button"
        class="nav-link"
        :class="{ selected: inbox }"
        :aria-pressed="inbox"
        @click="emit('location', null, true)"
      >
        <Inbox :size="18" aria-hidden="true" />收集箱
      </button>

      <div class="project-heading">
        <p class="nav-heading">项目与目标</p>
        <div class="project-create-actions">
          <button type="button" class="small-icon-button" title="新建项目" aria-label="新建项目"
            @click="emit('createProject', 'project')">
            <Plus :size="16" aria-hidden="true" />
          </button>
          <button type="button" class="small-icon-button" title="新建目标" aria-label="新建目标"
            @click="emit('createProject', 'goal')">
            <Target :size="16" aria-hidden="true" />
          </button>
        </div>
      </div>
      <ul class="project-navigation-list" aria-label="项目与目标列表">
        <li v-for="project in projects" :key="project.id">
          <button
            type="button"
            class="nav-link project-filter"
            :class="{ selected: selectedProjectId === project.id }"
            :aria-pressed="selectedProjectId === project.id"
            @click="emit('location', project.id, false)"
          >
            <Target v-if="project.kind === 'goal'" :size="17" aria-hidden="true" />
            <Folder v-else :size="17" aria-hidden="true" />
            <span>{{ project.name }}</span>
            <small>{{ project.open_task_count ?? 0 }}</small>
          </button>
          <button type="button" class="small-icon-button project-edit" :aria-label="`编辑${project.name}`"
            :title="`编辑${project.name}`" @click="emit('editProject', project)">
            <Pencil :size="14" aria-hidden="true" />
          </button>
        </li>
      </ul>
      <p v-if="!projects.length" class="navigation-empty">还没有项目或目标。</p>
    </div>

    <div class="task-account">
      <p class="task-account-email">{{ email }}</p>
      <button type="button" class="nav-link logout-button" :disabled="pendingLogout" @click="emit('logout')">
        <LogOut :size="18" aria-hidden="true" />{{ pendingLogout ? '正在退出…' : '退出登录' }}
      </button>
    </div>
  </aside>
</template>
