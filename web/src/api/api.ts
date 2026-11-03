// The one interface every screen talks to. Two implementations: live (HTTP to FastAPI) and mock
// (fixtures/mock_api). client.ts picks one from VITE_DATA.
import type { ItemFilters } from './filters';
import type {
  Channel,
  DispatchCreate,
  DispatchListOut,
  DispatchOut,
  DispatchPreviewOut,
  HealthOut,
  ItemListOut,
  ItemOut,
  ItemPatch,
  JobProgressOut,
  ModelCallListOut,
  OverlayOut,
  ProcessingSummaryOut,
  ProjectListOut,
  ProjectOut,
  RewalkSummaryOut,
  ShareItemOut,
  ShareLinkOut,
  TradeListOut,
  Trade,
  UploadOut,
  WalkDetailOut,
  WalkKind,
  WalkListOut,
} from './types';

export interface UploadRequest {
  kind: WalkKind;
  rewalkOf?: string | null;
  /** In walk order. */
  clips: File[];
}

/** 0..1 of bytes sent. */
export type UploadProgress = (fraction: number) => void;

export interface Api {
  health(): Promise<HealthOut>;
  projects(): Promise<ProjectListOut>;
  project(projectId: string): Promise<ProjectOut>;
  items(projectId: string, filters?: ItemFilters): Promise<ItemListOut>;
  item(itemId: string): Promise<ItemOut>;
  patchItem(itemId: string, patch: ItemPatch): Promise<ItemOut>;
  dismissItem(itemId: string): Promise<void>;
  shareItem(itemId: string): Promise<ShareLinkOut>;
  share(token: string): Promise<ShareItemOut>;
  walks(projectId?: string): Promise<WalkListOut>;
  walk(walkId: string): Promise<WalkDetailOut>;
  overlay(walkId: string): Promise<OverlayOut>;
  progress(walkId: string): Promise<JobProgressOut>;
  retryWalk(walkId: string): Promise<JobProgressOut>;
  modelCalls(walkId: string): Promise<ModelCallListOut>;
  processing(walkId: string): Promise<ProcessingSummaryOut>;
  rewalkSummary(walkId: string): Promise<RewalkSummaryOut>;
  upload(projectId: string, req: UploadRequest, onProgress?: UploadProgress): Promise<UploadOut>;
  trades(projectId: string): Promise<TradeListOut>;
  dispatchPreview(projectId: string, trade: Trade, channel?: Channel): Promise<DispatchPreviewOut>;
  dispatches(projectId: string, trade: Trade): Promise<DispatchListOut>;
  dispatch(projectId: string, trade: Trade, body: DispatchCreate): Promise<DispatchOut>;
}

/** An API error with the server's message, ready to show (DESIGN.md: say what happened and what to do). */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}
