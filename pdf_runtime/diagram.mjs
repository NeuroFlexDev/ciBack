// Normalize SVG with resvg/usvg: resolve markers and positioned text to paths
// before ReportLab sees it. No browser, external resolver or system font lookup.
import { Resvg } from '@resvg/resvg-js';
import { fileURLToPath } from 'node:url';

const MAX_INPUT = 1_500_000;
const MAX_OUTPUT = 12_000_000;
let input = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) {
  input += chunk;
  if (Buffer.byteLength(input, 'utf8') > MAX_INPUT) throw new Error('diagram_input_limit');
}
const fontPath = (name) => fileURLToPath(new URL(`../app/assets/pdf_fonts/${name}`, import.meta.url));
const renderer = new Resvg(input, {
  font: {
    loadSystemFonts: false,
    fontFiles: [fontPath('golos-text-400.ttf'), fontPath('golos-text-700.ttf')],
    defaultFontFamily: 'Golos Text', sansSerifFamily: 'Golos Text', serifFamily: 'Golos Text',
    monospaceFamily: 'Golos Text', cursiveFamily: 'Golos Text', fantasyFamily: 'Golos Text',
  },
  logLevel: 'off',
});
if (renderer.imagesToResolve().length || !Number.isFinite(renderer.width) || !Number.isFinite(renderer.height)
    || renderer.width <= 0 || renderer.height <= 0 || renderer.width >= 100000 || renderer.height >= 100000) {
  throw new Error('diagram_resource_or_dimension_limit');
}
// Text becomes positioned glyph paths and markers become ordinary geometry;
// the PDF retains sharp vector paths instead of dropping SVG-only semantics.
const normalized = renderer.toString();
if (Buffer.byteLength(normalized, 'utf8') > MAX_OUTPUT) throw new Error('diagram_output_limit');
process.stdout.write(JSON.stringify({ svg: normalized, width: renderer.width, height: renderer.height }));
