// Pure Node liteDOM rendering. No browser, remote fonts or source-directed loads.
import fs from 'node:fs';
global.MathJax = {
  loader: {
    paths: { mathjax: '@mathjax/src/bundle' },
    load: ['input/tex-base', '[tex]/ams', '[tex]/newcommand', '[tex]/textmacros', 'output/svg', 'adaptors/liteDOM'],
    require: (file) => import(file),
  },
  tex: { packages: ['base', 'ams', 'newcommand', 'textmacros'], maxBuffer: 12000, maxMacros: 1000 },
  output: { font: 'mathjax-newcm' },
  svg: { fontCache: 'none' },
  startup: { typeset: false },
};
try {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (!Array.isArray(input) || input.length > 300 || input.some(s => typeof s !== 'string' || s.length > 8000)) throw new Error('limit');
  await import('@mathjax/src/bundle/startup.js');
  await MathJax.startup.promise;
  const adaptor = MathJax.startup.adaptor;
  const output = [];
  for (const source of input) {
    // These macros are deliberately unavailable, including filesystem/URL input.
    if (/\\(?:require|input|include|href|url|html|class|cssId|style)\b/.test(source)) throw new Error('unsafe macro');
    const node = await MathJax.tex2svgPromise(source, { display: true, em: 14, ex: 7, containerWidth: 470 });
    const svg = adaptor.outerHTML(adaptor.tags(node, 'svg')[0]);
    if (/data-mjx-error|data-mml-node="merror"/.test(svg)) throw new Error('invalid expression');
    output.push(svg);
  }
  process.stdout.write(JSON.stringify(output));
} catch {
  process.stderr.write('Math could not be rendered safely.\n');
  process.exitCode = 1;
} finally { MathJax.done?.(); }
