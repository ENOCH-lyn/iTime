import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'
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
    { path: '/tasks', component: () => import('./pages/TasksPage.vue') },
    { path: '/calendar', component: () => import('./pages/CalendarPage.vue') },
    { path: '/account', component: () => import('./pages/AccountPage.vue') },
    { path: '/account/:section(password|devices|appearance)', component: () => import('./pages/AccountPage.vue') },
    { path: '/admin', redirect: '/admin/security' },
    ...['login', 'setup'].map(mode => ({
      path: `/admin/${mode}`, component: () => import('./pages/AdminAuthPage.vue'),
      props: { mode },
    })),
    { path: '/admin/:section(users|registration|ai|security|password|recovery|appearance)', component: () => import('./pages/AdminPage.vue') },
    { path: '/help', component: () => import('./pages/HelpPage.vue') },
    { path: '/:pathMatch(.*)*', component: () => import('./pages/NotFoundPage.vue') },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
createApp(App).use(router).mount('#app')
