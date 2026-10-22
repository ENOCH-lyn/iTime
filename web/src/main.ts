import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'
import './element-plus-theme.css'
import './lib/appearance'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./pages/ProductPage.vue') },
    { path: '/demo', component: () => import('./pages/DemoPage.vue') },
    ...['login', 'register', 'verify-email', 'resend-verification', 'forgot-password', 'reset-password'].map(mode => ({
      path: `/${mode}`, name: mode,
      component: () => import('./pages/AuthPage.vue'), props: { mode },
    })),
    { path: '/ai', component: () => import('./pages/AIPage.vue') },
    { path: '/timetable', component: () => import('./pages/TimetablePage.vue') },
    { path: '/timetable/month', component: () => import('./pages/CalendarPage.vue') },
    { path: '/tasks', component: () => import('./pages/TaskWorkspacePage.vue') },
    { path: '/tasks/full', component: () => import('./pages/TasksPage.vue') },
    { path: '/plans', redirect: '/timetable' },
    {
      path: '/calendar',
      redirect: to => ({ path: '/timetable/month', query: to.query }),
    },
    { path: '/focus', redirect: '/ai' },
    { path: '/reviews', redirect: '/ai' },
    { path: '/me', redirect: '/settings' },
    { path: '/me/:tab(.*)*', redirect: '/settings' },
    { path: '/stats', redirect: '/settings' },
    { path: '/settings', component: () => import('./pages/SettingsPage.vue') },
    { path: '/account', redirect: '/settings' },
    {
      path: '/account/:section(password|devices|privacy|quota|sync|data|appearance)',
      redirect: route => ({
        path: '/settings',
        hash: `#${String(route.params.section)}`,
      }),
    },
    { path: '/admin', redirect: '/admin/security' },
    ...['login', 'setup'].map(mode => ({
      path: `/admin/${mode}`, component: () => import('./pages/AdminAuthPage.vue'),
      props: { mode },
    })),
    { path: '/admin/quota', redirect: '/admin/security' },
    { path: '/admin/costs', redirect: '/admin/security' },
    { path: '/admin/logs', redirect: '/admin/security' },
    { path: '/admin/quality', redirect: '/admin/security' },
    { path: '/admin/:section(users|registration|ai|audit|security|password|recovery|appearance)', component: () => import('./pages/AdminPage.vue') },
    { path: '/help', redirect: '/settings' },
    { path: '/terms', redirect: '/settings' },
    { path: '/privacy', redirect: '/settings' },
    { path: '/:pathMatch(.*)*', component: () => import('./pages/NotFoundPage.vue') },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
createApp(App).use(router).mount('#app')
