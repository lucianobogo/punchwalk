// Landing page helpers: the URL the QR code opens, the QR itself, and the session flags that route a
// desktop visitor to the landing page once and a phone visitor straight into the demo project.
import QRCode from 'qrcode';

const LANDING_SEEN = 'punchwalk.landingSeen';
const DEMO_FLAG = 'punchwalk.demo';

/** The app's own address (APP_BASE_URL is the origin it's served from). */
export function appUrl(origin: string): string {
  return `${origin.replace(/\/+$/, '')}/`;
}

/** An SVG QR code in the given colors, as a data URL for an <img>. */
export async function qrDataUrl(text: string, dark: string): Promise<string> {
  const svg = await QRCode.toString(text, {
    type: 'svg',
    margin: 0,
    errorCorrectionLevel: 'M',
    color: { dark, light: '#ffffff00' },
  });
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function session(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export const markLandingSeen = () => session()?.setItem(LANDING_SEEN, '1');
export const landingSeen = () => session()?.getItem(LANDING_SEEN) === '1';
export const markDemo = () => session()?.setItem(DEMO_FLAG, '1');
export const isDemoSession = () => session()?.getItem(DEMO_FLAG) === '1';

export function isWide(minPx: number): boolean {
  return typeof window !== 'undefined' && Boolean(window.matchMedia?.(`(min-width: ${minPx}px)`).matches);
}

/** Inside the landing page's phone frame? Then never redirect: the frame shows the app itself. */
export function inFrame(): boolean {
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
}

/** Plain phrases for the three numbers under the headline (DESIGN.md "Landing page"). */
export const METRIC_PHRASE: Record<string, string> = {
  defects_found: 'defects found on our test walks',
  right_trade: 'sent to the right trade',
  false_claims_caught: 'false “it’s fixed” caught',
};
