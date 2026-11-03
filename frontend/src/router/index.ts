import { createRouter, createWebHistory, RouteRecordRaw } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

import Login from '@/views/Login.vue';
import StudyDashboard from '@/views/StudyDashboard.vue';
import MedicalWorkspace from '@/views/MedicalWorkspace.vue';
import AdminUsers from '@/views/AdminUsers.vue';

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: Login,
    meta: { public: true },
  },
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '/dashboard',
    name: 'StudyDashboard',
    component: StudyDashboard,
    meta: { requiresAuth: true },
  },
  {
    path: '/workspace/:studyId',
    name: 'MedicalWorkspace',
    component: MedicalWorkspace,
    meta: { requiresAuth: true },
  },
  {
    path: '/admin/users',
    name: 'AdminUsers',
    component: AdminUsers,
    meta: { requiresAuth: true, requiresAdmin: true },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to, _from, next) => {
  const authStore = useAuthStore();
  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    next('/login');
  } else if (to.meta.requiresAdmin && !authStore.isAdmin) {
    next('/dashboard');
  } else {
    next();
  }
});

export default router;
