// One-line demo banner (DESIGN.md "Landing page → Phone"). Shown only when the build sets VITE_DEMO=1;
// harmless otherwise. Dismissal is remembered per viewer.
import { X } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';

const KEY = 'punchwalk.demoBannerHidden';

function hidden(): boolean {
  try {
    return localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

export function DemoBanner() {
  const [gone, setGone] = useState(hidden);
  if (import.meta.env.VITE_DEMO !== '1' || gone) return null;
  return (
    <div className="mx-4 mt-2 flex items-center gap-2 rounded-card border border-hairline bg-sheet py-1 pr-1 pl-3">
      <p className="flex-1 text-secondary text-graphite">
        This is a demo project.{' '}
        <Link to="/record" className="font-medium text-tape-ink underline">
          Upload your own walk
        </Link>{' '}
        to try it live.
      </p>
      <button
        type="button"
        aria-label="Hide this message"
        className="flex size-11 shrink-0 items-center justify-center text-pencil"
        onClick={() => {
          setGone(true);
          try {
            localStorage.setItem(KEY, '1');
          } catch {
            // private mode: hidden until reload
          }
        }}
      >
        <X size={18} aria-hidden />
      </button>
    </div>
  );
}
