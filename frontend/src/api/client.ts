import axios from 'axios';
import { ElMessage } from 'element-plus';

declare module 'axios' {
  interface AxiosRequestConfig {
    suppressErrorToast?: boolean;
  }
}

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const rawDetail = error.response?.data?.detail;
    const detail = typeof rawDetail === 'string' ? rawDetail
      : Array.isArray(rawDetail) ? '请检查填写内容是否符合要求'
      : error.message || '请求服务发生异常';
    if (error.response?.status === 401) {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    if (!error.config?.suppressErrorToast) ElMessage.error(detail);
    return Promise.reject(error);
  }
);

export default api;
