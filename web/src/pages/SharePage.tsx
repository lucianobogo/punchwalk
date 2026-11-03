// Public, read-only item page for a trade (PLAN 6.1): what the link in Slack or email opens. Styled like
// item detail, built from ShareItemOut alone, with Play this moment and no edit actions or tab bar.
import { Play, TriangleAlert, Wrench } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { ApiError } from '../api/api';
import { useShare } from '../api/hooks';
import type { ShareItemOut } from '../api/types';
import { DetailList, EmptyState, Pill, Skeleton, StatusPill, TapeLogo } from '../components';
import { BeforeAfter } from '../features/item/BeforeAfter';
import { EvidencePhoto } from '../features/item/EvidencePhoto';
import { GROUNDING_LABEL } from '../features/item/actions';
import { PlayMomentSheet, type Playable } from '../features/play/PlayMomentSheet';
import { formatClock, formatShortDate } from '../lib/format';
import { SEVERITY_LABEL, STATUS_LABEL, TRADE_LABEL } from '../lib/labels';

export default function SharePage() {
  const { token } = useParams();
  const share = useShare(token);
  return (
    <div className="min-h-dvh pb-[calc(32px+var(--sab))]">
      <header className="flex items-center gap-3 px-4 pt-safe">
        <div className="flex min-h-14 w-full items-center gap-2.5 pt-2">
          <TapeLogo />
          <span className="text-row font-bold font-expanded text-graphite">Punchwalk</span>
          {share.data && <span className="ml-auto truncate text-secondary text-pencil">{share.data.project_name}</span>}
        </div>
      </header>
      {share.isLoading ? (
        <div className="px-4 pt-2" role="status" aria-label="Loading the item">
          <Skeleton className="aspect-[4/3] w-full rounded-card" />
          <Skeleton className="mt-5 h-7 w-3/4 rounded-full" />
        </div>
      ) : share.isError || !share.data ? (
        <EmptyState
          className="pt-20"
          title={share.error instanceof ApiError && share.error.status === 404 ? 'This link doesn’t open an item anymore.' : 'This item didn’t load.'}
          body="Ask the project manager to send it again."
        />
      ) : (
        <ShareBody s={share.data} />
      )}
    </div>
  );
}

export function ShareBody({ s }: { s: ShareItemOut }) {
  const [playing, setPlaying] = useState(false);
  const badge = s.status === 'needs_review' ? 'hold' : s.status === 'closed' ? 'signed' : null;
  const quoteAt = s.quote_start_s ?? s.video_start_s;
  const c = s.closure;
  const slider = s.status === 'closed' && c?.before_image_url && c.after_image_url;
  const playable: Playable = {
    id: s.token,
    origin_walk_id: null,
    number: s.number,
    title: s.title,
    video_url: s.video_url,
    video_start_s: s.video_start_s,
    video_end_s: s.video_end_s,
    quote: s.quote,
    quote_start_s: s.quote_start_s,
    quote_end_s: s.quote_end_s,
  };

  return (
    <>
      <div className="pt-3">
        {slider ? (
          <BeforeAfter
            before={c!.before_image_url!}
            after={c!.after_image_url!}
            beforeLabel={s.walked_at ? formatShortDate(s.walked_at) : 'Before'}
            afterLabel={c!.decided_at ? `${formatShortDate(c!.decided_at)}, re-walk` : 'Re-walk'}
            number={s.number}
            badge={badge}
            title={s.title}
          />
        ) : s.evidence ? (
          <EvidencePhoto evidence={s.evidence} number={s.number} badge={badge} alt={`Photo of item ${s.number}: ${s.title}`} />
        ) : null}
      </div>
      <div className="px-4">
        <div className="mt-5 flex flex-wrap gap-2">
          {s.status === 'closed' && <StatusPill status="closed" />}
          {s.severity === 'safety' && (
            <Pill tone="safety" icon={<TriangleAlert size={14} strokeWidth={2.25} aria-hidden />}>
              Safety
            </Pill>
          )}
        </div>
        <h1 className="mt-2 text-title font-semibold font-expanded-title text-graphite">{s.title}</h1>
        <p className="mt-1.5 text-body text-graphite">{s.description}</p>
        <p className="mt-4 flex items-start gap-2.5 text-card font-semibold text-graphite">
          <Wrench size={20} strokeWidth={2} className="mt-0.5 shrink-0" aria-hidden />
          <span>{s.action}</span>
        </p>
        {s.video_url && (
          <button
            type="button"
            onClick={() => setPlaying(true)}
            className="mt-4 flex w-full items-center gap-3 rounded-card border border-hairline bg-sheet p-4 text-left active:bg-primer"
          >
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-tape-tint text-tape-ink" aria-hidden>
              <Play size={14} fill="currentColor" strokeWidth={0} className="translate-x-px" />
            </span>
            <span className="min-w-0 flex-1">
              {s.quote && <span className="block text-body text-graphite">“{s.quote}”</span>}
              <span className="mt-0.5 block text-secondary tabular-nums text-pencil">Play this moment, {formatClock(quoteAt)}</span>
            </span>
          </button>
        )}
        <DetailList
          className="mt-4"
          rows={[
            { label: 'Project', value: s.project_address ?? s.project_name },
            { label: 'Room', value: s.room ?? 'Not set' },
            ...(s.location_detail ? [{ label: 'Where', value: s.location_detail }] : []),
            { label: 'Trade', value: s.company ? `${TRADE_LABEL[s.trade]}, ${s.company}` : TRADE_LABEL[s.trade] },
            { label: 'Severity', value: SEVERITY_LABEL[s.severity] },
            { label: 'Status', value: STATUS_LABEL[s.status] },
            ...(s.walked_at ? [{ label: 'Walked', value: formatShortDate(s.walked_at) }] : []),
            ...(s.evidence ? [{ label: 'Found by', value: GROUNDING_LABEL[s.evidence.grounding] }] : []),
          ]}
        />
        <p className="mt-6 text-center">
          <Link to="/" className="inline-flex min-h-11 items-center text-body text-tape-ink underline">
            Open in Punchwalk
          </Link>
        </p>
      </div>
      <PlayMomentSheet item={playable} open={playing} onOpenChange={setPlaying} />
    </>
  );
}
