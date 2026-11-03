// Every screen in DESIGN.md has a route here. Pages under TabLayout get the bottom tab bar;
// item detail has its own bottom action bar; share and landing stand alone.
import { Navigate, Outlet, ScrollRestoration, type RouteObject } from 'react-router';
import { TabBar } from './components';
import GalleryPage from './pages/GalleryPage';
import ItemDetailPage from './pages/ItemDetailPage';
import LandingPage from './pages/LandingPage';
import PunchListPage from './pages/PunchListPage';
import RecordPage from './pages/RecordPage';
import SharePage from './pages/SharePage';
import { TradePage, TradesPage } from './pages/TradesPage';
import WalkDetailPage from './pages/WalkDetailPage';
import ProcessedPage from './pages/ProcessedPage';
import { RewalkPage } from './pages/WalkSubPages';
import WalksPage from './pages/WalksPage';
import { EmptyState, ButtonLink } from './components';

/** Desktop (>= 768 px): the app in a centered 440 px column on Primer with hairline edges. */
function AppColumn() {
  return (
    <div className="min-h-dvh bg-primer md:mx-auto md:max-w-[var(--app-width)] md:border-x md:border-hairline">
      <ScrollRestoration />
      <Outlet />
    </div>
  );
}

function TabLayout() {
  return (
    <>
      <main className="pb-[calc(var(--tabbar-h)+var(--sab)+24px)]">
        <Outlet />
      </main>
      <TabBar />
    </>
  );
}

function NotFound() {
  return (
    <EmptyState
      className="pt-24"
      title="There’s no screen here."
      body="The link may be old. The punch list has everything that’s still open."
      action={<ButtonLink to="/">Open the punch list</ButtonLink>}
    />
  );
}

export const routes: RouteObject[] = [
  { path: '/landing', element: <LandingPage /> },
  {
    element: <AppColumn />,
    children: [
      {
        element: <TabLayout />,
        children: [
          { path: '/', element: <PunchListPage /> },
          { path: '/walks', element: <WalksPage /> },
          { path: '/walks/:id', element: <WalkDetailPage /> },
          { path: '/walks/:id/rewalk', element: <RewalkPage /> },
          { path: '/walks/:id/processing', element: <ProcessedPage /> },
          { path: '/record', element: <RecordPage /> },
          { path: '/trades', element: <TradesPage /> },
          { path: '/trades/:trade', element: <TradePage /> },
        ],
      },
      { path: '/items/:id', element: <ItemDetailPage /> },
      { path: '/share/item/:token', element: <SharePage /> },
      { path: '/dev/components', element: <GalleryPage /> },
      // manifest start_url (DESIGN.md "PWA")
      { path: '/app', element: <Navigate to="/" replace /> },
      { path: '*', element: <NotFound /> },
    ],
  },
];
