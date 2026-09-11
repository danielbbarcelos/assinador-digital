/**
 * Harness de teste: carrega o pdf.js de verdade (o mesmo arquivo que o app
 * serve), monta o viewport de uma página e devolve, em JSON:
 *
 *  - o `transform` do viewport;
 *  - o que `viewport.convertToPdfPoint` (pdf.js) responde nos cantos;
 *  - o que `coords.js` (nosso código) responde nos mesmos cantos.
 *
 * O teste em Python compara os três contra valores esperados. Rodar o pdf.js
 * real é o que dá garantia sobre /Rotate — replicar a matriz à mão não daria.
 *
 * Uso: node dump_viewport.mjs <arquivo.pdf> <scale> <saida.json>
 *
 * A saída vai para arquivo porque o pdf.js escreve avisos no stdout em Node.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.resolve(here, "../../app/static");

const pdfjs = await import(path.join(staticDir, "vendor/pdf.min.mjs"));
const coords = await import(path.join(staticDir, "coords.js"));

const [, , pdfPath, scaleArg, outPath, rectArg] = process.argv;
const scale = Number(scaleArg ?? 1);

pdfjs.GlobalWorkerOptions.workerSrc = path.join(staticDir, "vendor/pdf.worker.min.mjs");

const data = new Uint8Array(fs.readFileSync(pdfPath));
const doc = await pdfjs.getDocument({ data, isEvalSupported: false }).promise;
const page = await doc.getPage(1);
const viewport = page.getViewport({ scale });

// Os quatro cantos do canvas, em coordenadas de viewport.
const corners = {
  topLeft: { x: 0, y: 0 },
  topRight: { x: viewport.width, y: 0 },
  bottomLeft: { x: 0, y: viewport.height },
  bottomRight: { x: viewport.width, y: viewport.height },
};

const out = {
  pageRotate: page.rotate,
  scale,
  viewport: { width: viewport.width, height: viewport.height, transform: viewport.transform },
  pdfjs: {},
  coords: {},
};

for (const [name, pt] of Object.entries(corners)) {
  const [px, py] = viewport.convertToPdfPoint(pt.x, pt.y);
  out.pdfjs[name] = { x: px, y: py };
  const mine = coords.applyInverseTransform(pt.x, pt.y, viewport.transform);
  out.coords[name] = mine;
}

// Uma caixa arrastada de baixo para cima e da direita para a esquerda, para
// exercitar a normalização.
out.box = coords.viewportRectToPdfBox(corners.bottomRight, corners.topLeft, viewport);

// Conversão de evento de mouse com canvas escalado por CSS (caso HiDPI).
out.clientToViewport = coords.clientToViewport(
  120, // clientX
  80, // clientY
  { left: 20, top: 30, width: viewport.width / 2, height: viewport.height / 2 },
  viewport,
);

// Retângulo arbitrário em coordenadas de viewport ("x1,y1,x2,y2"), para o
// teste de ponta a ponta: o que sai daqui é assinado de verdade no PDF.
if (rectArg) {
  const [rx1, ry1, rx2, ry2] = rectArg.split(",").map(Number);
  out.sampleBox = coords.viewportRectToPdfBox({ x: rx1, y: ry1 }, { x: rx2, y: ry2 }, viewport);
}

await doc.destroy();
const json = JSON.stringify(out, null, 2);
if (outPath) {
  fs.writeFileSync(outPath, json);
} else {
  process.stdout.write(json);
}
