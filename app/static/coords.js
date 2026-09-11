/**
 * Conversão de coordenadas tela → PDF.
 *
 * Três sistemas de coordenadas se encontram aqui, e confundi-los é o bug
 * clássico deste tipo de app:
 *
 *  1. **Cliente**  — `evt.clientX/clientY`, pixels CSS da janela.
 *  2. **Viewport** — o espaço de renderização do pdf.js: origem no canto
 *     SUPERIOR esquerdo, `y` crescendo para baixo, já multiplicado por `scale`.
 *  3. **PDF**      — pontos (1/72"), origem no canto INFERIOR esquerdo, e
 *     afetado pela rotação `/Rotate` da página.
 *
 * Não usamos `evt.offsetX`: ele mente quando o canvas tem tamanho CSS diferente
 * do tamanho em pixels (HiDPI, zoom do navegador, `max-width` no layout). O
 * caminho correto é `getBoundingClientRect()`.
 *
 * A matriz `viewport.transform` do pdf.js já embute escala E rotação; invertê-la
 * é exatamente o que `viewport.convertToPdfPoint` faz internamente. Mantemos a
 * conta aqui, em função pura, para ser testável fora do browser.
 *
 * @module coords
 */

/**
 * Ponto do evento do mouse em coordenadas de viewport do pdf.js.
 *
 * @param {number} clientX
 * @param {number} clientY
 * @param {{left:number, top:number, width:number, height:number}} rect
 *   Retângulo do canvas na tela (`canvas.getBoundingClientRect()`).
 * @param {{width:number, height:number}} viewport
 *   Viewport do pdf.js usado para renderizar a página.
 * @returns {{x:number, y:number}}
 */
export function clientToViewport(clientX, clientY, rect, viewport) {
  // rect está em pixels CSS; o viewport, em unidades de renderização. A razão
  // entre os dois absorve devicePixelRatio, zoom e qualquer escala do layout.
  const ratioX = viewport.width / rect.width;
  const ratioY = viewport.height / rect.height;
  return {
    x: (clientX - rect.left) * ratioX,
    y: (clientY - rect.top) * ratioY,
  };
}

/**
 * Aplica a inversa de uma matriz de transformação 2D do PDF.
 *
 * A matriz `[a, b, c, d, e, f]` mapeia (x_pdf, y_pdf) → (x_view, y_view) assim:
 *
 *     x_view = a * x_pdf + c * y_pdf + e
 *     y_view = b * x_pdf + d * y_pdf + f
 *
 * Aqui fazemos o caminho de volta.
 *
 * @param {number} x
 * @param {number} y
 * @param {number[]} transform  `viewport.transform`
 * @returns {{x:number, y:number}} ponto em pontos PDF
 */
export function applyInverseTransform(x, y, transform) {
  const [a, b, c, d, e, f] = transform;
  const det = a * d - b * c;
  if (det === 0) {
    throw new Error("Matriz de transformação degenerada");
  }
  const px = x - e;
  const py = y - f;
  return {
    x: (px * d - py * c) / det,
    y: (py * a - px * b) / det,
  };
}

/**
 * Ponto de tela → ponto PDF, usando o viewport da página renderizada.
 *
 * Prefere `viewport.convertToPdfPoint` (pdf.js) quando existe; a conta local é
 * idêntica e serve de fallback e de alvo de teste.
 *
 * @returns {{x:number, y:number}}
 */
export function viewportToPdfPoint(x, y, viewport) {
  if (typeof viewport.convertToPdfPoint === "function") {
    const [px, py] = viewport.convertToPdfPoint(x, y);
    return { x: px, y: py };
  }
  return applyInverseTransform(x, y, viewport.transform);
}

/**
 * Retângulo desenhado na tela → caixa da assinatura em pontos PDF.
 *
 * Os dois pontos são os cantos opostos do arrasto, em coordenadas de viewport.
 * O resultado sai sempre normalizado (x1 < x2, y1 < y2) — o usuário pode
 * arrastar em qualquer direção — e é isso que o backend espera receber.
 *
 * @returns {{x1:number, y1:number, x2:number, y2:number}}
 */
export function viewportRectToPdfBox(start, end, viewport) {
  const a = viewportToPdfPoint(start.x, start.y, viewport);
  const b = viewportToPdfPoint(end.x, end.y, viewport);
  return {
    x1: Math.min(a.x, b.x),
    y1: Math.min(a.y, b.y),
    x2: Math.max(a.x, b.x),
    y2: Math.max(a.y, b.y),
  };
}

/** Retângulo normalizado em coordenadas de viewport (para desenhar na tela). */
export function normalizeRect(start, end) {
  return {
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(end.x - start.x),
    height: Math.abs(end.y - start.y),
  };
}

/**
 * Ponto PDF → ponto de viewport (o caminho de volta).
 *
 * É o que permite guardar a seleção em pontos PDF — a única representação que
 * não muda quando o usuário redimensiona a janela e a página é re-renderizada
 * com outro `scale`.
 */
export function pdfToViewportPoint(x, y, viewport) {
  if (typeof viewport.convertToViewportPoint === "function") {
    const [vx, vy] = viewport.convertToViewportPoint(x, y);
    return { x: vx, y: vy };
  }
  const [a, b, c, d, e, f] = viewport.transform;
  return { x: a * x + c * y + e, y: b * x + d * y + f };
}

/** Caixa em pontos PDF → retângulo de viewport pronto para desenhar. */
export function pdfBoxToViewportRect(box, viewport) {
  const a = pdfToViewportPoint(box.x1, box.y1, viewport);
  const b = pdfToViewportPoint(box.x2, box.y2, viewport);
  return normalizeRect(a, b);
}
