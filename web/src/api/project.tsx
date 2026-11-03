// The project the punch list, trades and record screens are about. Remembered per device
// (localStorage is a per-viewer convenience); falls back to the first project.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { useProjects } from './hooks';

const KEY = 'punchwalk.project';

interface ProjectCtx {
  projectId: string | undefined;
  setProjectId: (id: string) => void;
  isLoading: boolean;
  /** loading -> ready | empty (no projects on this server) | error (the list didn't load). */
  status: 'loading' | 'ready' | 'empty' | 'error';
  error: unknown;
  retry: () => void;
}

const Ctx = createContext<ProjectCtx | null>(null);

function readSaved(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function ProjectProvider({ children }: { children: ReactNode }) {
  const projects = useProjects();
  const [chosen, setChosen] = useState<string | null>(readSaved);
  const list = projects.data?.projects ?? [];
  const projectId = list.find((p) => p.id === chosen)?.id ?? list[0]?.id;

  const setProjectId = useCallback((id: string) => {
    setChosen(id);
    try {
      localStorage.setItem(KEY, id);
    } catch {
      // private mode: the choice lasts until reload
    }
  }, []);

  const status: ProjectCtx['status'] = projects.isError
    ? 'error'
    : projects.isLoading || !projects.data
      ? 'loading'
      : list.length
        ? 'ready'
        : 'empty';
  const { refetch } = projects;
  const value = useMemo(
    () => ({ projectId, setProjectId, isLoading: projects.isLoading, status, error: projects.error, retry: () => void refetch() }),
    [projectId, setProjectId, projects.isLoading, status, projects.error, refetch],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCurrentProject(): ProjectCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error('useCurrentProject needs <ProjectProvider>');
  return v;
}
