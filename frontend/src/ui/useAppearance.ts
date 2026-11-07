import { ref } from 'vue';
export type Appearance = 'system' | 'light' | 'dark';
const saved = localStorage.getItem('medimaging.appearance');
export const appearance = ref<Appearance>(saved === 'light' || saved === 'dark' ? saved : 'system');
const media = window.matchMedia('(prefers-color-scheme: dark)');
export const systemDark = ref(media.matches);
media.addEventListener('change', event => { systemDark.value = event.matches; });
window.addEventListener('storage', event => {
  if (event.key === 'medimaging.appearance') appearance.value = event.newValue === 'dark' || event.newValue === 'light' ? event.newValue : 'system';
});
export function setAppearance(value: Appearance) {
  appearance.value = value;
  localStorage.setItem('medimaging.appearance', value);
}
