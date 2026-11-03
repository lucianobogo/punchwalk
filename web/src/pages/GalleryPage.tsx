// /dev/components: every shared component in every state, plus the tokens, so one screenshot is
// a design-system check against DESIGN.md.
import { MoreHorizontal, Pencil, Search, Wrench } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import {
  ActionBar,
  Button,
  Card,
  CheckOption,
  Chip,
  DetailList,
  EmptyState,
  EvidenceThumb,
  IconButton,
  LargeTitleBar,
  ListRow,
  MiniRow,
  Pill,
  PlayButton,
  ProgressBar,
  PushedBar,
  SectionHeader,
  SegmentedControl,
  SeverityPill,
  Sheet,
  SheetAction,
  SkeletonRows,
  StatusPill,
  TabBar,
  TapeLogo,
  TapeTab,
  ToastView,
  VideoFrame,
  useToast,
} from '../components';
import { useItems, useWalks } from '../api/hooks';
import { useCurrentProject } from '../api/project';
import { DATA_MODE } from '../api/client';
import { COLOR_TOKENS, TYPE_SCALE } from '../lib/tokens';
import { formatClock } from '../lib/format';
import { TRADE_LABEL } from '../lib/labels';
import type { Trade } from '../api/types';

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-hairline px-4 py-6" aria-labelledby={`g-${title}`}>
      <h2 id={`g-${title}`} className="mb-4 text-card font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Swatch({ name, token, use }: { name: string; token: string; use: string }) {
  const [hex, setHex] = useState('');
  useEffect(() => {
    const raw = getComputedStyle(document.documentElement).getPropertyValue(`--color-${token}`).trim().toUpperCase();
    // The minifier shortens #FFFFFF to #FFF; show the six-digit form DESIGN.md uses.
    setHex(/^#[0-9A-F]{3}$/.test(raw) ? `#${[...raw.slice(1)].map((c) => c + c).join('')}` : raw);
  }, [token]);
  return (
    <div className="flex items-center gap-3">
      <span className="size-10 shrink-0 rounded-thumb border border-hairline" style={{ background: `var(--color-${token})` }} />
      <span className="min-w-0">
        <span className="block text-row font-semibold">{name}</span>
        <span className="block truncate text-secondary text-pencil">
          <span className="tabular-nums">{hex}</span> {use}
        </span>
      </span>
    </div>
  );
}

export default function GalleryPage() {
  const toast = useToast();
  const { projectId } = useCurrentProject();
  const items = useItems(projectId);
  const walks = useWalks(projectId);
  const poster = walks.data?.walks.find((w) => w.media.poster)?.media.poster;
  const [segment, setSegment] = useState<'todo' | 'signed_off' | 'all'>('todo');
  const [channel, setChannel] = useState<'slack' | 'email' | 'outbox'>('slack');
  const [sheet, setSheet] = useState<'none' | 'filter' | 'more' | 'night'>('none');
  const [trades, setTrades] = useState<Trade[]>(['electrical']);
  const list = items.data?.items ?? [];
  const pick = (n: number) => list.find((i) => i.number === n);
  const facets = items.data?.facets.trade ?? {};

  return (
    <div className="pb-16">
      <PushedBar
        backTo="/"
        backLabel="Punch list"
        title="Components"
        subtitle={`Design system check. Data: ${DATA_MODE}.`}
      />

      <Section title="Color">
        <div className="grid grid-cols-1 gap-3">
          {COLOR_TOKENS.map((t) => (
            <Swatch key={t.token} {...t} />
          ))}
        </div>
      </Section>

      <Section title="Type">
        <div className="flex flex-col gap-4">
          {TYPE_SCALE.map((t) => (
            <div key={t.name}>
              <p className="text-secondary text-pencil">
                {t.name}, {t.spec}
              </p>
              <p className={t.className}>{t.sample}</p>
            </div>
          ))}
          <p className="text-secondary text-pencil">Tabular numerals: <span className="text-body tabular-nums text-graphite">0:14 1:32 $0.43 11 12</span></p>
        </div>
      </Section>

      <Section title="Tape tab">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-5">
          <TapeTab number={7} />
          <TapeTab number={12} />
          <TapeTab number={108} />
          <TapeTab number={2} badge="hold" />
          <TapeTab number={3} badge="signed" />
          <TapeTab number={7} size="sm" />
          <TapeTab number={11} size="sm" />
          <TapeTab number={2} size="sm" badge="hold" />
          <TapeTab number={1} size="sm" badge="signed" />
          <span className="flex items-center gap-2">
            <TapeLogo />
            <span className="text-card font-bold font-expanded">Punchwalk</span>
          </span>
        </div>
        <div className="mt-5 flex gap-5">
          <EvidenceThumb src={pick(2)?.evidence?.crop_url} number={2} badge="hold" />
          <EvidenceThumb src={pick(8)?.evidence?.crop_url} number={8} />
          <EvidenceThumb number={12} badge="hold" />
        </div>
      </Section>

      <Section title="Buttons">
        <div className="flex flex-col gap-3">
          <Button size="lg" block>
            Sign off
          </Button>
          <div className="flex gap-3">
            <Button className="flex-1">Send again</Button>
            <Button variant="secondary" className="flex-1">
              Open item
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button busy busyLabel="Sending…">
              Send
            </Button>
            <Button disabled>Disabled</Button>
            <Button variant="quiet">Keep watching</Button>
            <IconButton label="Search" tone="track">
              <Search size={20} aria-hidden />
            </IconButton>
            <IconButton label="More actions" tone="bordered" square size={48}>
              <MoreHorizontal size={22} aria-hidden />
            </IconButton>
            <IconButton label="Edit item">
              <Pencil size={22} className="text-tape-ink" aria-hidden />
            </IconButton>
            <PlayButton label="Play this moment, 0:14" />
          </div>
        </div>
      </Section>

      <Section title="Chips and pills">
        <div className="flex flex-wrap gap-2">
          <Chip label="Trade" onOpen={() => setSheet('filter')} />
          <Chip label="Severity" onOpen={() => {}} />
          <Chip label="Electrical" active onOpen={() => setSheet('filter')} onClear={() => {}} />
          <Chip label="Safety" count={1} active onOpen={() => {}} onClear={() => {}} />
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <SeverityPill severity="safety" />
          <SeverityPill severity="functional" />
          <StatusPill status="open" />
          <StatusPill status="needs_review" />
          <StatusPill status="closed" />
          <Pill>Muse Voice Transcribe</Pill>
          <Pill>SAM 3.1</Pill>
          <Pill>Muse Spark Open on Baseten</Pill>
        </div>
      </Section>

      <Section title="Segmented control">
        <div className="flex flex-col gap-3">
          <SegmentedControl
            label="Show"
            value={segment}
            onChange={setSegment}
            options={[
              { value: 'todo', label: 'To do', count: items.data?.counts.todo ?? 6 },
              { value: 'signed_off', label: 'Signed off', count: items.data?.counts.signed_off ?? 6 },
              { value: 'all', label: 'All', count: items.data?.counts.all ?? 12 },
            ]}
          />
          <SegmentedControl
            label="Send by"
            value={channel}
            onChange={setChannel}
            options={[
              { value: 'slack', label: 'Slack' },
              { value: 'email', label: 'Email' },
              { value: 'outbox', label: 'Copy link' },
            ]}
          />
        </div>
      </Section>

      <Section title="Rows">
        <div className="flex flex-col gap-2">
          <SectionHeader tone="hold" count={2}>
            Needs a look
          </SectionHeader>
          <ListRow
            to="/items/itm_2b_02"
            number={2}
            badge="hold"
            thumbSrc={pick(2)?.evidence?.crop_url}
            title="Coffee drips on counter"
            summary="Marked fixed on the re-walk, but the drips are still visible."
            onPlay={() => {}}
          />
          <SectionHeader className="mt-4" count={1}>
            Bedroom
          </SectionHeader>
          <ListRow
            to="/items/itm_2b_11"
            number={11}
            thumbSrc={pick(11)?.evidence?.crop_url}
            title="Outlet cover cracked"
            summary="Electrical. Left of the bed."
            pill={<SeverityPill severity="safety" />}
            onPlay={() => {}}
          />
          <ListRow
            to="/items/itm_2b_07"
            number={7}
            selected
            thumbSrc={pick(7)?.evidence?.crop_url}
            title="Scuff marks on wall (selected)"
            summary="Painting. Left of the closet door."
            onPlay={() => {}}
          />
          <Card padded={false} className="mt-2 divide-y divide-hairline">
            <MiniRow number={1} badge="signed" title="Missing cabinet knob" trailing={<span className="text-signed-ink">Signed off</span>} />
            <MiniRow number={5} title="Scuff marks on wall" trailing={formatClock(81)} />
          </Card>
          <div className="mt-2">
            <SkeletonRows count={1} label="Loading the punch list" />
          </div>
        </div>
      </Section>

      <Section title="Cards">
        <div className="flex flex-col gap-3">
          <Card>
            <p className="flex items-center gap-2 text-body font-semibold">
              <Wrench size={18} aria-hidden /> Install a matching knob
            </p>
            <p className="mt-1 text-secondary text-pencil">Default card: Sheet, 1 px Hairline, radius 14, no shadow.</p>
          </Card>
          <Card tone="flagged">
            <SectionHeader tone="hold" as="div">
              Needs a look
            </SectionHeader>
            <p className="mt-2 text-body">Marked fixed on the re-walk, but the defect is still visible.</p>
          </Card>
          <DetailList
            rows={[
              { label: 'Room', value: 'Kitchen' },
              { label: 'Trade', value: TRADE_LABEL.carpentry_millwork },
              { label: 'Status', value: 'Open, sent to Oakline Millwork Nov 3' },
            ]}
          />
          <div className="flex flex-col gap-2">
            <p className="text-secondary text-pencil">Muse Spark Open on Baseten, 7 of 12 written</p>
            <ProgressBar value={7 / 12} label="Writing items" />
          </div>
        </div>
      </Section>

      <Section title="Empty states and toast">
        <Card padded={false}>
          <EmptyState
            title="No items match these filters."
            action={<Button variant="secondary">Clear filters</Button>}
          />
        </Card>
        <div className="mt-3">
          <ToastView toast={{ message: 'Sent 3 items to Ace Electric', tone: 'success' }} />
        </div>
        <div className="mt-3">
          <Button variant="secondary" onClick={() => toast.show('Sent 3 items to Ace Electric')}>
            Show a toast
          </Button>
        </div>
      </Section>

      <Section title="Sheets">
        <div className="flex flex-wrap gap-3">
          <Button variant="secondary" onClick={() => setSheet('filter')}>
            Open filter sheet
          </Button>
          <Button variant="secondary" onClick={() => setSheet('more')}>
            Open More sheet
          </Button>
          <Button variant="secondary" onClick={() => setSheet('night')}>
            Open dark sheet
          </Button>
        </div>
      </Section>

      <Section title="Video frame">
        <VideoFrame
          aspect="3/4"
          rounded
          poster={poster}
          label="Walk video placeholder"
        >
          <div className="absolute top-3 left-3">
            <TapeTab number={6} />
          </div>
        </VideoFrame>
      </Section>

      <Section title="Bars">
        <div className="flex flex-col gap-4">
          <div className="overflow-hidden rounded-card border border-hairline bg-primer">
            <LargeTitleBar title="Unit 2B final punch" onTitleClick={() => {}} titleLabel="Switch project" fixed={false} />
            <div className="h-3" />
          </div>
          <div className="overflow-hidden rounded-card border border-hairline bg-primer pb-3">
            <PushedBar backTo="/walks" backLabel="Walks" title="First walk" meta="Nov 3, 12 items" sticky={false} />
          </div>
          <div className="overflow-hidden rounded-card border border-hairline">
            <TabBar fixed={false} activePath="/" />
          </div>
          <div className="overflow-hidden rounded-card border border-hairline">
            <ActionBar fixed={false}>
              <Button size="lg" className="flex-1">
                Sign off
              </Button>
              <IconButton label="More actions" tone="bordered" square size={48}>
                <MoreHorizontal size={22} aria-hidden />
              </IconButton>
            </ActionBar>
          </div>
        </div>
      </Section>

      <Sheet
        open={sheet === 'filter'}
        onOpenChange={(o) => !o && setSheet('none')}
        title="Trade"
        footer={
          <Button size="lg" block onClick={() => setSheet('none')}>
            {`Show ${trades.reduce((n, t) => n + (facets[t] ?? 0), 0)} items`}
          </Button>
        }
      >
        <div className="px-4">
          {(Object.keys(facets) as Trade[]).map((t) => (
            <CheckOption
              key={t}
              label={TRADE_LABEL[t]}
              count={facets[t]}
              checked={trades.includes(t)}
              onChange={(c) => setTrades((prev) => (c ? [...prev, t] : prev.filter((x) => x !== t)))}
            />
          ))}
        </div>
      </Sheet>

      <Sheet open={sheet === 'more'} onOpenChange={(o) => !o && setSheet('none')} title="More actions">
        <SheetAction onClick={() => setSheet('none')}>Send again</SheetAction>
        <SheetAction onClick={() => setSheet('none')}>Mark as needs a look</SheetAction>
        <SheetAction onClick={() => setSheet('none')}>Edit</SheetAction>
      </Sheet>

      <Sheet
        open={sheet === 'night'}
        onOpenChange={(o) => !o && setSheet('none')}
        title="Missing cabinet knob"
        tone="night"
        closable
        leading={<TapeTab number={1} decorative />}
      >
        <VideoFrame aspect="3/4" poster={poster} />
        <div className="flex items-center justify-between px-4 py-4">
          <Button variant="night-quiet">Replay</Button>
          <PlayButton label="Play" tone="night" size={56} />
          <Button variant="quiet" className="text-tape-tab">
            Keep watching
          </Button>
        </div>
      </Sheet>
    </div>
  );
}
