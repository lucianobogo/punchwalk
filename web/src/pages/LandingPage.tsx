// Desktop landing page (PLAN 8.2). Placeholder header so the route and the desktop context exist.
import { Link } from 'react-router';
import { TapeLogo } from '../components';
import { ComingSoon } from './common';

export default function LandingPage() {
  return (
    <div className="min-h-dvh bg-primer">
      <header className="mx-auto flex max-w-[1200px] items-center justify-between px-6 pt-6 lg:px-16">
        <Link to="/landing" className="flex items-center gap-3">
          <TapeLogo />
          <span className="text-card font-bold font-expanded text-graphite">Punchwalk</span>
        </Link>
        <Link to="/" className="inline-flex min-h-11 items-center text-body text-tape-ink">
          Open the demo project
        </Link>
      </header>
      <ComingSoon task="8.2" what="Walk it once. Get a punch list with proof." />
    </div>
  );
}
