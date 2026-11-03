// Token names for the component gallery. Values live only in styles/index.css; the gallery reads
// them back from the CSS so it shows what the app really uses.
export const COLOR_TOKENS: { name: string; token: string; use: string }[] = [
  { name: 'Primer', token: 'primer', use: 'App background' },
  { name: 'Sheet', token: 'sheet', use: 'Cards, rows, sheets, bars' },
  { name: 'Graphite', token: 'graphite', use: 'Text, icons, item numbers' },
  { name: 'Pencil', token: 'pencil', use: 'Secondary text' },
  { name: 'Hairline', token: 'hairline', use: 'Borders, dividers' },
  { name: 'Track', token: 'track', use: 'Segment track, grey buttons' },
  { name: 'Tape tab', token: 'tape-tab', use: 'Item-number tab fill' },
  { name: 'Tape', token: 'tape', use: 'Hand outline, pointer dot, Record, progress' },
  { name: 'Tape ink', token: 'tape-ink', use: 'Links, primary buttons' },
  { name: 'Tape tint', token: 'tape-tint', use: 'Play buttons, selected' },
  { name: 'Redline', token: 'redline', use: 'Defect outlines' },
  { name: 'Safety', token: 'safety', use: 'Safety pill text' },
  { name: 'Safety bg', token: 'safety-bg', use: 'Safety pill' },
  { name: 'Signed off', token: 'signed', use: 'Signed-off fills' },
  { name: 'Signed ink', token: 'signed-ink', use: 'Signed-off text' },
  { name: 'Hold', token: 'hold', use: 'Needs-a-look dot' },
  { name: 'Hold ink', token: 'hold-ink', use: 'Needs-a-look text' },
  { name: 'Hold card', token: 'hold-card', use: 'Flagged card' },
  { name: 'Hold border', token: 'hold-border', use: 'Flagged card border' },
  { name: 'Night', token: 'night', use: 'Play this moment sheet' },
];

export const TYPE_SCALE: { name: string; className: string; spec: string; sample: string }[] = [
  { name: 'Top-level title', className: 'text-display font-semibold font-expanded', spec: '28/32 Expanded 125% semibold', sample: 'Unit 2B final punch' },
  { name: 'Pushed title', className: 'text-title font-semibold font-expanded-title', spec: '22/28 Expanded 112% semibold', sample: 'Missing cabinet knob' },
  { name: 'Card title', className: 'text-card font-semibold', spec: '17/22 semibold', sample: 'Door doesn’t latch' },
  { name: 'Row title', className: 'text-row font-semibold', spec: '15/20 semibold', sample: 'Coffee drips on counter' },
  { name: 'Body', className: 'text-body', spec: '15/21 regular', sample: 'The upper cabinet left of the sink is missing its knob.' },
  { name: 'Secondary', className: 'text-secondary text-pencil', spec: '13/18 regular, Pencil', sample: 'Painting. Left of the closet door.' },
  { name: 'Section header', className: 'text-section font-medium text-pencil', spec: '13/16 medium, Pencil', sample: 'Bedroom 2' },
  { name: 'Tab bar label', className: 'text-tab font-medium', spec: '11/13 medium', sample: 'Punch list' },
];
