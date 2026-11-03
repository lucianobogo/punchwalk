// react-query hooks: one per read, one per write. Keys are arrays starting with the resource name,
// so invalidating ['items'] refreshes every filtered list of every project.
import {
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query';
import { ApiError, type UploadProgress, type UploadRequest } from './api';
import { api } from './client';
import { filtersKey, type ItemFilters } from './filters';
import type { Channel, DispatchCreate, ItemOut, ItemPatch, JobProgressOut, Trade } from './types';

export const qk = {
  health: () => ['health'] as const,
  projects: () => ['projects'] as const,
  project: (id: string) => ['project', id] as const,
  items: (projectId: string, f: ItemFilters = {}) => ['items', projectId, filtersKey(f)] as const,
  item: (id: string) => ['item', id] as const,
  share: (token: string) => ['share', token] as const,
  walks: (projectId?: string) => ['walks', projectId ?? 'all'] as const,
  walk: (id: string) => ['walk', id] as const,
  overlay: (id: string) => ['overlay', id] as const,
  progress: (id: string) => ['progress', id] as const,
  modelCalls: (id: string) => ['modelCalls', id] as const,
  processing: (id: string) => ['processing', id] as const,
  rewalk: (id: string) => ['rewalk', id] as const,
  trades: (projectId: string) => ['trades', projectId] as const,
  dispatchPreview: (projectId: string, trade: Trade, channel?: Channel) =>
    ['dispatchPreview', projectId, trade, channel ?? 'default'] as const,
  dispatches: (projectId: string, trade: Trade) => ['dispatches', projectId, trade] as const,
};

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      // One retry for flaky networks, none for "not found": that answer won't change.
      queries: { staleTime: 30_000, retry: (n, e) => n < 1 && !(e instanceof ApiError && e.status === 404), refetchOnWindowFocus: false },
      mutations: { retry: 0 },
    },
  });
}

type Opts<T> = Omit<UseQueryOptions<T, Error, T, readonly unknown[]>, 'queryKey' | 'queryFn'>;
const on = (...ids: (string | undefined | null)[]) => ids.every(Boolean);

export const useHealth = () => useQuery({ queryKey: qk.health(), queryFn: () => api.health() });
export const useProjects = () => useQuery({ queryKey: qk.projects(), queryFn: () => api.projects() });
export const useProject = (id: string | undefined) =>
  useQuery({ queryKey: qk.project(id ?? ''), queryFn: () => api.project(id!), enabled: on(id) });

export const useItems = (projectId: string | undefined, filters: ItemFilters = {}) =>
  useQuery({
    queryKey: qk.items(projectId ?? '', filters),
    queryFn: () => api.items(projectId!, filters),
    enabled: on(projectId),
    placeholderData: (prev) => prev,
  });

export const useItem = (id: string | undefined, opts?: Opts<ItemOut>) =>
  useQuery({ queryKey: qk.item(id ?? ''), queryFn: () => api.item(id!), enabled: on(id), ...opts });

export const useShare = (token: string | undefined) =>
  useQuery({ queryKey: qk.share(token ?? ''), queryFn: () => api.share(token!), enabled: on(token) });

export const useWalks = (projectId?: string) =>
  useQuery({ queryKey: qk.walks(projectId), queryFn: () => api.walks(projectId) });

export const useWalk = (id: string | undefined) =>
  useQuery({ queryKey: qk.walk(id ?? ''), queryFn: () => api.walk(id!), enabled: on(id) });

export const useOverlay = (id: string | undefined) =>
  useQuery({ queryKey: qk.overlay(id ?? ''), queryFn: () => api.overlay(id!), enabled: on(id), staleTime: Infinity });

const isFinished = (p: JobProgressOut | undefined) => p?.status === 'done' || p?.status === 'failed';

/** Polls every 1.5 s until the job is done or failed. */
export const useProgress = (walkId: string | undefined, intervalMs = 1500) =>
  useQuery({
    queryKey: qk.progress(walkId ?? ''),
    queryFn: () => api.progress(walkId!),
    enabled: on(walkId),
    staleTime: 0,
    refetchInterval: (q) => (isFinished(q.state.data) ? false : intervalMs),
  });

export const useModelCalls = (id: string | undefined) =>
  useQuery({ queryKey: qk.modelCalls(id ?? ''), queryFn: () => api.modelCalls(id!), enabled: on(id) });

export const useProcessing = (id: string | undefined) =>
  useQuery({ queryKey: qk.processing(id ?? ''), queryFn: () => api.processing(id!), enabled: on(id) });

export const useRewalkSummary = (id: string | undefined) =>
  useQuery({ queryKey: qk.rewalk(id ?? ''), queryFn: () => api.rewalkSummary(id!), enabled: on(id) });

export const useTrades = (projectId: string | undefined) =>
  useQuery({ queryKey: qk.trades(projectId ?? ''), queryFn: () => api.trades(projectId!), enabled: on(projectId) });

export const useDispatchPreview = (projectId: string | undefined, trade: Trade | undefined, channel?: Channel) =>
  useQuery({
    queryKey: qk.dispatchPreview(projectId ?? '', trade ?? 'general', channel),
    queryFn: () => api.dispatchPreview(projectId!, trade!, channel),
    enabled: on(projectId, trade),
  });

export const useDispatches = (projectId: string | undefined, trade: Trade | undefined) =>
  useQuery({
    queryKey: qk.dispatches(projectId ?? '', trade ?? 'general'),
    queryFn: () => api.dispatches(projectId!, trade!),
    enabled: on(projectId, trade),
  });

/** Everything an item change can show up in. */
function invalidateItemViews(qc: QueryClient) {
  for (const key of ['items', 'item', 'project', 'projects', 'walk', 'trades', 'dispatchPreview', 'dispatches', 'rewalk']) {
    qc.invalidateQueries({ queryKey: [key] });
  }
}

export function usePatchItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ItemPatch }) => api.patchItem(id, patch),
    onSuccess: (item) => {
      qc.setQueryData(qk.item(item.id), item);
      invalidateItemViews(qc);
    },
  });
}

export function useDismissItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.dismissItem(id),
    onSuccess: () => invalidateItemViews(qc),
  });
}

export function useShareItem() {
  return useMutation({ mutationFn: (id: string) => api.shareItem(id) });
}

export function useSendDispatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, trade, body }: { projectId: string; trade: Trade; body: DispatchCreate }) =>
      api.dispatch(projectId, trade, body),
    onSuccess: () => invalidateItemViews(qc),
  });
}

export function useApproveAndSend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, channel }: { itemId: string; channel?: Channel }) => api.approveAndSend(itemId, channel),
    onSuccess: () => invalidateItemViews(qc),
  });
}

export function useUploadWalk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, req, onProgress }: { projectId: string; req: UploadRequest; onProgress?: UploadProgress }) =>
      api.upload(projectId, req, onProgress),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['walks'] }),
  });
}

export function useRetryWalk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (walkId: string) => api.retryWalk(walkId),
    onSuccess: (p) => {
      qc.setQueryData(qk.progress(p.walk_id), p);
      qc.invalidateQueries({ queryKey: ['walks'] });
    },
  });
}
