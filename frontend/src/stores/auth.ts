import { defineStore } from 'pinia';
import api from '@/api/client';

export interface User {
  id: number;
  username: string;
  real_name: string;
  role: 'ADMIN' | 'DOCTOR';
  department: string;
  is_active: boolean;
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: JSON.parse(localStorage.getItem('user') || 'null') as User | null,
  }),
  getters: {
    isAuthenticated: (state) => !!state.token,
    isAdmin: (state) => state.user?.role === 'ADMIN',
    isDoctor: (state) => state.user?.role === 'DOCTOR' || state.user?.role === 'ADMIN',
  },
  actions: {
    async login(username: string, password: string) {
      const res: any = await api.post('/auth/login', { username, password });
      this.token = res.access_token;
      this.user = res.user;
      localStorage.setItem('token', this.token);
      localStorage.setItem('user', JSON.stringify(this.user));
      return res.user;
    },
    logout() {
      this.token = '';
      this.user = null;
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      window.location.href = '/login';
    },
    async fetchMe() {
      try {
        const user: any = await api.get('/auth/me');
        this.user = user;
        localStorage.setItem('user', JSON.stringify(user));
      } catch (err) {
        this.logout();
      }
    }
  }
});
