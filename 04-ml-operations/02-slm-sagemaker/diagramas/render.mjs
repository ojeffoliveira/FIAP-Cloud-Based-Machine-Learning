// Render headless de .excalidraw -> PNG.
// Usa exportToCanvas (decodifica os files/icones antes de pintar);
// exportToBlob/exportToSvg falham com icones embutidos.
// Uso: node render.mjs <entrada.excalidraw> <saida.png>
//
// Deps (instalar nesta pasta, nao ha package.json versionado):
//   npm i puppeteer @excalidraw/excalidraw@0.17.6
// A versao do Excalidraw esta fixada porque da 0.18 em diante o bundle saiu de
// dist/excalidraw.production.min.js e o addScriptTag abaixo nao acha mais o arquivo.
import puppeteer from 'puppeteer';
import fs from 'fs';

const inPath = process.argv[2];
const outPath = process.argv[3];
if (!inPath || !outPath) { console.error('uso: node render.mjs <in.excalidraw> <out.png>'); process.exit(1); }

const data = fs.readFileSync(inPath, 'utf8');
const libPath = new URL('./node_modules/@excalidraw/excalidraw/dist/excalidraw.production.min.js', import.meta.url).pathname;

const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 1100, deviceScaleFactor: 2 });
await page.setContent('<!DOCTYPE html><html><head></head><body></body></html>');
// React/ReactDOM UMD que a lib Excalidraw espera no escopo global
await page.addScriptTag({ url: 'https://unpkg.com/react@18/umd/react.production.min.js' }).catch(() => {});
await page.addScriptTag({ url: 'https://unpkg.com/react-dom@18/umd/react-dom.production.min.js' }).catch(() => {});
await page.addScriptTag({ path: libPath });

const dataUrl = await page.evaluate(async (sceneStr) => {
  const scene = JSON.parse(sceneStr);
  const api = window.ExcalidrawLib;
  if (!api) return 'NO_LIB';
  if (!api.exportToCanvas) return 'NO_FN:' + Object.keys(api).join(',');
  const canvas = await api.exportToCanvas({
    elements: scene.elements,
    appState: { ...scene.appState, exportBackground: true, exportPadding: 24 },
    files: scene.files || {},
    getDimensions: (w, h) => ({ width: w * 2, height: h * 2, scale: 2 }),
  });
  return canvas.toDataURL('image/png');
}, data);

if (typeof dataUrl !== 'string' || !dataUrl.startsWith('data:image')) {
  console.error('FALHA no render:', dataUrl);
  await browser.close();
  process.exit(2);
}
fs.writeFileSync(outPath, Buffer.from(dataUrl.split(',')[1], 'base64'));
console.error('PNG OK:', outPath);
console.log(outPath);
await browser.close();
