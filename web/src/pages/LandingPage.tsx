// Landing page (DESIGN.md "Landing page", 09-landing-desktop.png). Desktop: the pitch, three numbers
// from eval/report.md, two buttons, a QR code, and the live app running in a generic phone frame.
// Phone: straight into the demo project, with the one-line demo banner.
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { useProcessing, useProjects, useWalks } from '../api/hooks';
import { useCurrentProject } from '../api/project';
import { ButtonLink, Pill, TapeLogo } from '../components';
import { METRIC_PHRASE, appUrl, isWide, markDemo, markLandingSeen, qrDataUrl } from '../features/landing/landing';
import { evalVisibility } from './ProcessedPage';

const FRAME_W = 393;
const FRAME_H = 852;
const BEZEL = 12;

export default function LandingPage() {
  const [wide] = useState(() => isWide(768));
  const { setProjectId } = useCurrentProject();
  const projects = useProjects();
  const demoId = projects.data?.projects[0]?.id;
  const navigate = useNavigate();

  useEffect(() => {
    if (wide) {
      markLandingSeen();
      return;
    }
    // A phone goes straight into the demo project, with the banner.
    // Into the app either way: if the projects didn't load, the punch list shows the error with Try again.
    if (!projects.data && !projects.isError) return;
    markDemo();
    if (demoId) setProjectId(demoId);
    navigate('/', { replace: true });
  }, [wide, projects.data, projects.isError, demoId, setProjectId, navigate]);

  if (!wide) return null;
  return <DesktopLanding demoProjectId={demoId} />;
}

function DesktopLanding({ demoProjectId }: { demoProjectId: string | undefined }) {
  const walks = useWalks(demoProjectId);
  const demoWalk = walks.data?.walks.find((w) => w.kind === 'initial' && w.status === 'ready');
  const processing = useProcessing(demoWalk?.id);
  const ev = processing.data?.eval ?? null;
  const evMode = evalVisibility(ev);
  const url = useMemo(() => appUrl(window.location.origin), []);
  const [qr, setQr] = useState<string | null>(null);
  const scale = useFrameScale();

  useEffect(() => {
    const dark = getComputedStyle(document.documentElement).getPropertyValue('--color-graphite').trim() || 'black';
    qrDataUrl(url, dark).then(setQr, () => setQr(null));
  }, [url]);

  return (
    <div className="min-h-dvh bg-primer">
      <header className="mx-auto flex max-w-[1240px] items-center justify-between px-8 pt-8 lg:px-16">
        <Link to="/landing" className="flex items-center gap-3" aria-label="Punchwalk">
          <TapeLogo />
          <span className="text-card font-bold font-expanded text-graphite">Punchwalk</span>
        </Link>
        <a href="#how" className="inline-flex min-h-11 items-center text-body text-tape-ink">
          How it works
        </a>
      </header>

      <main className="mx-auto grid max-w-[1240px] items-center gap-12 px-8 pt-6 pb-16 lg:grid-cols-[minmax(0,1fr)_auto] lg:gap-20 lg:px-16">
        <section aria-labelledby="hero-h" className="max-w-[640px]">
          <h1 id="hero-h" className="text-hero font-semibold font-expanded text-graphite">
            <span className="block">Walk it once.</span>
            <span className="block">Get a punch list with proof.</span>
          </h1>
          <p className="mt-6 max-w-[560px] text-card font-normal text-pencil">
            Point at each defect, say what’s wrong, and record the walk on Ray-Ban Meta glasses or a phone. Punchwalk writes the punch list,
            outlines every defect on its photo, and sends each trade its own list. On the re-walk it signs off what was fixed.
          </p>

          {evMode !== 'hidden' && ev && (
            <div className="mt-8">
              <dl className="grid max-w-[600px] grid-cols-3 gap-5">
                {ev.metrics.slice(0, 3).map((m) => (
                  <div key={m.key} className="border-t-2 border-graphite pt-3">
                    <dt className="sr-only">{m.label}</dt>
                    <dd>
                      <span className="block text-display font-semibold font-expanded tabular-nums text-graphite">
                        {m.value} of {m.total}
                      </span>
                      <span className="mt-1 block text-body text-pencil">{METRIC_PHRASE[m.key] ?? m.label.toLowerCase()}</span>
                    </dd>
                  </div>
                ))}
              </dl>
              {evMode === 'sample' && <p className="mt-2 text-secondary text-pencil">Sample numbers from the mock data.</p>}
            </div>
          )}

          <div className="mt-8 flex flex-wrap gap-3">
            <ButtonLink to="/" size="lg">
              Open the demo project
            </ButtonLink>
            <ButtonLink to="/record" size="lg" variant="secondary">
              Upload your own walk
            </ButtonLink>
          </div>

          <div className="mt-8 flex items-center gap-5">
            <div className="flex size-[104px] shrink-0 items-center justify-center rounded-card border border-hairline bg-sheet p-3">
              {qr ? <img src={qr} alt={`QR code for ${url}`} className="size-full" /> : <span className="text-secondary text-pencil">QR code</span>}
            </div>
            <div>
              <p className="text-card font-semibold text-graphite">Open on your phone</p>
              <p className="text-body text-pencil">It’s built for the phone a project manager already carries.</p>
            </div>
          </div>

          <p className="mt-8 max-w-[600px] text-secondary text-pencil">
            Built with Muse Voice Transcribe, SAM 3.1 and Muse Spark Open on Baseten during the Meta Global AI Developer Hackathon, Nov 3–4,
            2026.
          </p>
        </section>

        <div className="justify-self-center" style={{ width: (FRAME_W + BEZEL * 2) * scale, height: (FRAME_H + BEZEL * 2) * scale }}>
          <div
            className="origin-top-left overflow-hidden rounded-[56px] bg-graphite"
            style={{ width: FRAME_W + BEZEL * 2, height: FRAME_H + BEZEL * 2, padding: BEZEL, transform: `scale(${scale})` }}
          >
            <iframe
              src="/"
              title="Punchwalk, the live app"
              className="block rounded-[44px] bg-primer"
              style={{ width: FRAME_W, height: FRAME_H, border: 0 }}
            />
          </div>
        </div>
      </main>

      <section id="how" aria-labelledby="how-h" className="border-t border-hairline bg-sheet">
        <div className="mx-auto max-w-[1240px] px-8 py-14 lg:px-16">
          <h2 id="how-h" className="text-display font-semibold font-expanded text-graphite">
            How it works
          </h2>
          <ol className="mt-8 grid gap-6 md:grid-cols-3">
            {[
              ['Muse Voice Transcribe', 'Listens to the walk', 'Every sentence, with when it was said.'],
              ['SAM 3.1', 'Follows your hand', 'Finds each place you pointed and outlines the defect next to your fingertip.'],
              ['Muse Spark Open on Baseten', 'Writes and checks each item', 'Summary, action, trade, room and severity, then checks each item against its own photo and quote.'],
            ].map(([model, title, body]) => (
              <li key={model} className="rounded-card border border-hairline p-5">
                <p className="text-card font-semibold text-graphite">{title}</p>
                <Pill className="mt-2">{model}</Pill>
                <p className="mt-2 text-body text-pencil">{body}</p>
              </li>
            ))}
          </ol>
          <Links />
        </div>
      </section>
    </div>
  );
}

function Links() {
  const repo = import.meta.env.VITE_REPO_URL;
  const video = import.meta.env.VITE_VIDEO_URL;
  if (!repo && !video) {
    return <p className="mt-8 text-body text-pencil">The code and the demo video are linked here with the submission.</p>;
  }
  return (
    <p className="mt-8 flex gap-6 text-body">
      {repo && (
        <a href={repo} className="inline-flex min-h-11 items-center text-tape-ink underline">
          The code on GitHub
        </a>
      )}
      {video && (
        <a href={video} className="inline-flex min-h-11 items-center text-tape-ink underline">
          The 3-minute demo video
        </a>
      )}
    </p>
  );
}

/** Scale the phone frame to fit the window height (it's 876 px tall at full size). */
function useFrameScale() {
  const calc = () => Math.min(1, Math.max(0.55, (window.innerHeight - 120) / (FRAME_H + BEZEL * 2)));
  const [s, setS] = useState(calc);
  useEffect(() => {
    const on = () => setS(calc());
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return s;
}
