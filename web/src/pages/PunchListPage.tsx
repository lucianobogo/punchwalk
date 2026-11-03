// Punch list (home), DESIGN.md "Punch list" and docs/design/01-punch-list.png.
// Segments, filter chips with sheets, search/group/sort, Needs a look pinned first, compact rows with a
// play button that opens Play this moment in place. Every filter lives in the URL.
import { Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router';
import { applyFilters, type Segment } from '../api/filters';
import { useItems, useProject, useProjects } from '../api/hooks';
import { useCurrentProject } from '../api/project';
import type { ItemOut } from '../api/types';
import {
  Button,
  ButtonLink,
  Chip,
  EmptyState,
  IconButton,
  LargeTitleBar,
  ListRow,
  SectionHeader,
  SegmentedControl,
  SeverityPill,
  SkeletonRows,
} from '../components';
import { DemoBanner } from '../features/punchlist/DemoBanner';
import { groupItems, rowSummary } from '../features/punchlist/group';
import {
  DIMENSION_LABEL,
  FilterSheet,
  ProjectSheet,
  SearchSheet,
  optionLabel,
  type Dimension,
} from '../features/punchlist/sheets';
import { clearFilters, hasFilters, matchesSent, viewFromParams, viewToParams, type ListView } from '../features/punchlist/view';
import { PlayMomentSheet } from '../features/play/PlayMomentSheet';
import { formatClock } from '../lib/format';
import { ErrorState } from './common';

const DIMENSIONS: Dimension[] = ['trade', 'severity', 'room'];

export default function PunchListPage() {
  const { projectId, setProjectId } = useCurrentProject();
  const projects = useProjects();
  const project = useProject(projectId);
  const items = useItems(projectId);
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const paramKey = params.toString();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const view = useMemo(() => viewFromParams(new URLSearchParams(paramKey)), [paramKey]);
  const [sheet, setSheet] = useState<Dimension | 'search' | 'project' | null>(null);
  const [playing, setPlaying] = useState<ItemOut | null>(null);

  const setView = (next: ListView) => setParams(viewToParams(next), { replace: true });

  const all = items.data?.items ?? [];
  const counts = items.data?.counts;
  const roomOrder = project.data?.rooms ?? [];
  const list = useMemo(() => {
    if (!projectId) return [];
    return applyFilters(all, projectId, { ...view.filters, status: undefined, sort: undefined })
      .items.filter((i) => matchesSent(i, view.sent));
  }, [all, projectId, view]);
  const shown = useMemo(() => {
    const seg = view.segment;
    const inSeg = list.filter((i) => (seg === 'all' ? true : seg === 'todo' ? i.status !== 'closed' : i.status === 'closed'));
    return groupItems(inSeg, { group: view.group, sort: view.filters.sort, roomOrder });
  }, [list, view, roomOrder]);
  const shownCount = shown.reduce((n, s) => n + s.items.length, 0);

  const firstRun = project.data != null && project.data.walk_count === 0 && (counts?.all ?? 0) === 0;
  const loading = items.isLoading || !projectId;

  return (
    <>
      <LargeTitleBar
        title={project.data?.name ?? 'Punch list'}
        onTitleClick={(projects.data?.projects.length ?? 0) > 1 ? () => setSheet('project') : undefined}
        titleLabel={project.data ? `${project.data.name}. Switch project.` : undefined}
      />
      <DemoBanner />

      {!firstRun && (
        <div className="px-4 pt-3">
          <SegmentedControl<Segment>
            label="Show"
            value={view.segment}
            onChange={(segment) => setView({ ...view, segment })}
            options={[
              { value: 'todo', label: 'To do', count: counts?.todo },
              { value: 'signed_off', label: 'Signed off', count: counts?.signed_off },
              { value: 'all', label: 'All', count: counts?.all },
            ]}
          />
          <div className="mt-3 flex items-center gap-2">
            <div className="-my-1 flex min-w-0 flex-1 gap-2 overflow-x-auto py-1 [scrollbar-width:none]">
              {view.filters.q && (
                <Chip
                  label={`“${view.filters.q}”`}
                  active
                  onOpen={() => setSheet('search')}
                  onClear={() => setView({ ...view, filters: { ...view.filters, q: undefined } })}
                  clearLabel="Clear search"
                />
              )}
              {view.sent !== 'any' && (
                <Chip
                  label={view.sent === 'yes' ? 'Sent' : 'Not sent yet'}
                  active
                  onOpen={() => setSheet('search')}
                  onClear={() => setView({ ...view, sent: 'any' })}
                  clearLabel="Clear sent filter"
                />
              )}
              {DIMENSIONS.map((dim) => {
                const values = view.filters[dim] ?? [];
                const active = values.length > 0;
                const label = !active
                  ? DIMENSION_LABEL[dim]
                  : values.length === 1
                    ? optionLabel(dim, values[0])
                    : `${DIMENSION_LABEL[dim]} ${values.length}`;
                return (
                  <Chip
                    key={dim}
                    label={label}
                    active={active}
                    onOpen={() => setSheet(dim)}
                    onClear={() => setView({ ...view, filters: { ...view.filters, [dim]: undefined } })}
                    clearLabel={`Clear ${DIMENSION_LABEL[dim].toLowerCase()} filter`}
                  />
                );
              })}
            </div>
            <IconButton label="Search, group and sort" tone="track" onClick={() => setSheet('search')}>
              <Search size={20} strokeWidth={2} aria-hidden />
            </IconButton>
          </div>
        </div>
      )}

      <div className="px-4 pt-4">
        {loading ? (
          <SkeletonRows label="Loading the punch list" count={5} />
        ) : items.isError ? (
          <ErrorState error={items.error} onRetry={() => items.refetch()} />
        ) : firstRun ? (
          <EmptyState
            title="No walks yet."
            body="Point at each problem, say what’s wrong, and record the walk."
            action={<ButtonLink to="/record">Record a walk</ButtonLink>}
          />
        ) : shownCount === 0 ? (
          hasFilters(view) ? (
            <EmptyState
              title="No items match these filters."
              action={
                <Button variant="secondary" onClick={() => setView(clearFilters(view))}>
                  Clear filters
                </Button>
              }
            />
          ) : view.segment === 'todo' ? (
            <EmptyState
              title="Everything is signed off."
              action={
                <Button variant="secondary" onClick={() => setView({ ...view, segment: 'signed_off' })}>
                  See signed-off items
                </Button>
              }
            />
          ) : (
            <EmptyState title="Nothing is signed off yet." body="Items show up here after you sign them off or a re-walk confirms the fix." />
          )
        ) : (
          <div className="flex flex-col">
            {shown.map((section, si) => (
              <section key={section.key} aria-label={section.title ?? 'Items in walk order'} className={si > 0 ? 'mt-5' : undefined}>
                {section.title && (
                  <SectionHeader tone={section.tone} count={section.items.length} className="mb-2.5">
                    {section.title}
                  </SectionHeader>
                )}
                <ul className="flex flex-col gap-2">
                  {section.items.map((it) => (
                    <li key={it.id}>
                      <ListRow
                        to={`/items/${it.id}`}
                        linkState={{ from: `${location.pathname}${location.search}` }}
                        number={it.number}
                        badge={it.status === 'needs_review' ? 'hold' : it.status === 'closed' ? 'signed' : null}
                        thumbSrc={it.evidence?.crop_url}
                        title={it.title}
                        summary={rowSummary(it, view.group)}
                        pill={it.severity === 'safety' ? <SeverityPill severity="safety" /> : undefined}
                        onPlay={it.video_url ? () => setPlaying(it) : undefined}
                        playLabel={`Play this moment for item ${it.number}, ${formatClock(it.quote_start_s ?? it.video_start_s)}`}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>

      <FilterSheet
        dim={sheet && sheet !== 'search' && sheet !== 'project' ? sheet : null}
        onClose={() => setSheet(null)}
        all={all}
        view={view}
        roomOrder={roomOrder}
        onApply={setView}
      />
      <SearchSheet open={sheet === 'search'} onClose={() => setSheet(null)} all={all} view={view} onApply={setView} />
      <ProjectSheet
        open={sheet === 'project'}
        onClose={() => setSheet(null)}
        projects={projects.data?.projects ?? []}
        currentId={projectId}
        onPick={(id) => {
          setProjectId(id);
          setView(clearFilters({ ...view, segment: 'todo' }));
        }}
      />
      <PlayMomentSheet item={playing} open={playing != null} onOpenChange={(o) => !o && setPlaying(null)} />
    </>
  );
}
