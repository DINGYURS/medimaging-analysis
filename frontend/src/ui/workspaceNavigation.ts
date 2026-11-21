export function positiveId(value: unknown): number | null {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  const n = Number(value);
  return Number.isSafeInteger(n) && n > 0 ? n : null;
}

export interface WorkspaceView {
  studyId: number; taskId: number; slice: number; windowWidth: number; windowCenter: number;
  preset: string; zoom: number; selectedId: number | null; activeTab: string; sort: string;
  collapsed: boolean; showMask: boolean; maskOpacity: number;
}

// History state carries only display settings; study/task access is checked by the API.
export function readWorkspaceView(value: unknown, studyId: number, taskId: number | null, maxSlices: number, lesionIds: number[]): WorkspaceView | null {
  const v = value as WorkspaceView | null;
  if (!v || v.studyId !== studyId || v.taskId !== taskId) return null;
  if (!Number.isInteger(v.slice) || v.slice < 1 || v.slice > maxSlices ||
      !Number.isFinite(v.windowWidth) || v.windowWidth <= 0 || !Number.isFinite(v.windowCenter) ||
      !Number.isFinite(v.zoom) || v.zoom < .6 || v.zoom > 2 ||
      !Number.isFinite(v.maskOpacity) || v.maskOpacity < 10 || v.maskOpacity > 90 ||
      typeof v.preset !== 'string' || typeof v.collapsed !== 'boolean' || typeof v.showMask !== 'boolean' ||
      !['lesions', 'features', 'insights'].includes(v.activeTab) || !['volume', 'slice'].includes(v.sort)) return null;
  return { ...v, activeTab: v.activeTab === 'features' ? 'lesions' : v.activeTab, selectedId: lesionIds.includes(v.selectedId as number) ? v.selectedId : null };
}
