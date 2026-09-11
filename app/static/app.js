/**
 * Assinador — interface.
 *
 * Decisões que valem saber antes de mexer aqui:
 *
 * * **A marca é guardada em pontos PDF**, não em pixels. Pixel muda quando a
 *   janela é redimensionada ou o zoom muda; ponto PDF não muda. Para desenhar,
 *   converte-se de volta (ver `coords.js`).
 * * **A conversão de coordenadas mora em `coords.js`** e é testada fora do
 *   browser, com o pdf.js de verdade, incluindo páginas com /Rotate.
 * * **O tamanho da assinatura é do app**, não do usuário: ele só escolhe onde
 *   ela entra. O tamanho vem de `/api/health`, que é quem sabe o que o carimbo
 *   precisa para caber.
 * * Certificado e senha só existem aqui até o `fetch` — nada em localStorage,
 *   nada em URL. Guardar no cofre é uma escolha explícita, e quem cifra é o
 *   servidor local.
 */

import * as pdfjsLib from "/static/vendor/pdf.min.mjs";
import {
  clientToViewport,
  pdfBoxToViewportRect,
  viewportToPdfPoint,
} from "/static/coords.js";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdf.worker.min.mjs";

const $ = (id) => document.getElementById(id);

const el = {
  app: document.querySelector(".app"),
  desk: $("desk"),
  dropzone: $("dropzone"),
  stage: $("stage"),
  wrap: $("canvas-wrap"),
  canvas: $("page-canvas"),
  mark: $("selection"),
  markName: $("mark-name"),
  markDoc: $("mark-doc"),
  markDate: $("mark-date"),
  markReason: $("mark-reason"),
  markLocation: $("mark-location"),
  pages: $("pages"),
  pagesList: $("pages-list"),
  docbar: $("docbar"),
  pdfName: $("pdf-name"),
  pageNum: $("page-num"),
  pageTotal: $("page-total"),
  pagerNum: $("pager-num"),
  pagerTotal: $("pager-total"),
  pager: $("pager"),
  prev: $("prev-page"),
  next: $("next-page"),
  changePdf: $("change-pdf"),
  pickPdf: $("pick-pdf"),
  zoom: $("zoom"),
  zoomIn: $("zoom-in"),
  zoomOut: $("zoom-out"),
  zoomLevel: $("zoom-level"),
  guide: $("guide"),
  spot: $("spot"),
  spotText: $("spot-text"),
  clear: $("clear-selection"),
  form: $("form"),
  certChoices: $("cert-choices"),
  certUpload: $("cert-upload"),
  pfx: $("pfx"),
  pfxPick: $("pfx-pick"),
  pfxName: $("pfx-name"),
  remember: $("remember"),
  reason: $("reason"),
  location: $("location"),
  timestamp: $("timestamp"),
  tsaWarn: $("tsa-warn"),
  submit: $("submit"),
  submitLabel: document.querySelector(".seal-btn__label"),
  signedNote: $("signed-note"),
  // certificados
  certlist: $("certlist"),
  addcert: $("addcert"),
  newPfx: $("new-pfx"),
  newPfxPick: $("new-pfx-pick"),
  newPfxName: $("new-pfx-name"),
  newPassword: $("new-password"),
  newToggle: $("new-toggle"),
  addCertBtn: $("add-cert-btn"),
  // validação
  validateDrop: $("validate-drop"),
  pickValidate: $("pick-validate"),
  validateResult: $("validate-result"),
  // diálogo
  dialog: $("dialog"),
  dialogBody: $("dialog-body"),
  dialogTitle: $("dialog-title"),
  dialogText: $("dialog-text"),
  dialogNote: $("dialog-note"),
  dialogActions: $("dialog-actions"),
  dialogForm: $("dialog-form"),
  dialogPassword: $("dialog-password"),
  dialogPasswordToggle: $("dialog-password-toggle"),
  dialogRememberWrap: $("dialog-remember-wrap"),
  dialogRemember: $("dialog-remember"),
  dialogRisk: $("dialog-risk"),
  dialogWorking: $("dialog-working"),
  dialogWorkingText: $("dialog-working-text"),
  // cadastro de certificado
  newStorePassword: $("new-store-password"),
  newRisk: $("new-risk"),
  newRiskOk: $("new-risk-ok"),
};

const state = {
  view: "sign",
  file: null,
  doc: null,
  page: 1,
  pageCount: 0,
  viewport: null,
  renderTask: null,
  zoom: null,
  /** @type {{page:number, box:{x1,y1,x2,y2}}[]} — em pontos PDF, uma por página */
  marks: [],
  /** tamanho da assinatura em pontos, vindo do servidor */
  markSize: { width: 280, height: 96 },
  certificates: [],
  chosenCert: null, // id do cofre, ou "upload"
  busy: false,
  /** depois de assinar: o documento na tela é o assinado, não o original */
  signed: null,
  downloadUrl: null,
  lastSignedName: null,
  thumbs: new Map(),
};

// ---------------------------------------------------------------------------
// Diálogo central
// ---------------------------------------------------------------------------

/**
 * Um diálogo com título, texto e botões. É o lugar onde o app diz que algo
 * terminou ou deu errado — inline, essas mensagens passam despercebidas.
 *
 * @param {{title:string, text:string, note?:string, kind?:string,
 *          actions:{label:string, style?:string, onClick?:Function}[]}} opcoes
 */
function showDialog({
  title,
  text,
  note,
  kind = "",
  actions = [],
  working = false,
  workingText = "",
  form = null,
}) {
  el.dialog.className = `dialog ${kind ? `dialog--${kind}` : ""}`.trim();
  el.dialogTitle.textContent = title;
  el.dialogText.textContent = text || "";
  el.dialogText.hidden = !text;
  el.dialogNote.hidden = !note;
  el.dialogNote.textContent = note || "";

  // formulário de senha, quando o diálogo é quem pergunta
  el.dialogForm.hidden = !form;
  if (form) {
    el.dialogPassword.value = "";
    el.dialogPassword.type = "password";
    el.dialogPasswordToggle.textContent = "ver";
    el.dialogRememberWrap.hidden = !form.offerRemember;
    el.dialogRemember.checked = false;
    el.dialogRisk.hidden = true;
  }

  // progresso: some tudo que se clica, fica só o que se lê
  el.dialogWorking.hidden = !working;
  el.dialogWorkingText.textContent = workingText || "Assinando";

  el.dialogActions.innerHTML = "";
  el.dialogActions.hidden = actions.length === 0;
  for (const acao of actions) {
    const botao = document.createElement("button");
    botao.type = "button";
    botao.className = acao.style === "primary" ? "seal-btn" : "quiet-btn";
    botao.textContent = acao.label;
    botao.disabled = Boolean(acao.disabled);
    if (acao.id) botao.id = acao.id;
    botao.addEventListener("click", async () => {
      if (acao.keepOpen !== true) el.dialog.close();
      await acao.onClick?.();
    });
    el.dialogActions.appendChild(botao);
  }
  if (!el.dialog.open) el.dialog.showModal();
  if (form) setTimeout(() => el.dialogPassword.focus(), 60);
}

/** Troca o conteúdo do diálogo aberto por um estado de trabalho. */
function dialogWorking(title, texto) {
  showDialog({ kind: "done", title, working: true, workingText: texto, actions: [] });
}

el.dialogPasswordToggle.addEventListener("click", () =>
  togglePassword(el.dialogPassword, el.dialogPasswordToggle),
);

// guardar a senha é uma escolha com consequência, e ela aparece ao marcar
el.dialogRemember.addEventListener("change", () => {
  el.dialogRisk.hidden = !el.dialogRemember.checked;
});

/**
 * Pergunta a senha do certificado no meio da tela, não na barra lateral.
 *
 * Resolve com `{ senha, guardar }`, ou com `null` se a pessoa desistir. O
 * diálogo continua aberto: quem chama troca o conteúdo por um progresso, e a
 * transição fica contínua.
 */
function askPassword({ titulo, texto, offerRemember }) {
  return new Promise((resolve) => {
    let respondido = false;
    const responder = (valor) => {
      if (respondido) return;
      respondido = true;
      resolve(valor);
    };

    showDialog({
      title: titulo,
      text: texto,
      form: { offerRemember },
      actions: [
        { label: "Cancelar", onClick: () => responder(null) },
        {
          label: "Assinar",
          style: "primary",
          keepOpen: true,
          id: "dialog-confirm",
          onClick: () => {
            if (!el.dialogPassword.value) {
              el.dialogPassword.focus();
              return;
            }
            responder({
              senha: el.dialogPassword.value,
              guardar: el.dialogRemember.checked,
            });
          },
        },
      ],
    });

    // Enter confirma, Esc desiste
    el.dialogPassword.onkeydown = (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        $("dialog-confirm")?.click();
      }
    };
    el.dialog.addEventListener("close", () => responder(null), { once: true });
  });
}

function showError(payload) {
  clearFieldErrors();
  if (payload.field) {
    document.querySelector(`[data-field="${payload.field}"]`)?.classList.add("has-error");
  }
  showDialog({
    kind: "error",
    title: payload.title || "Não deu para assinar",
    text: payload.error || "Erro desconhecido.",
    note: payload.detail || payload.code,
    actions: [{ label: "Entendi", style: "primary" }],
  });

}

// ---------------------------------------------------------------------------
// Navegação entre telas
// ---------------------------------------------------------------------------

for (const aba of document.querySelectorAll(".tab")) {
  aba.addEventListener("click", () => goTo(aba.dataset.goto));
}

function goTo(view) {
  state.view = view;
  el.app.dataset.view = view;
  for (const aba of document.querySelectorAll(".tab")) {
    aba.setAttribute("aria-current", aba.dataset.goto === view ? "true" : "false");
  }
  $("view-certs").hidden = view !== "certs";
  $("view-validate").hidden = view !== "validate";
  if (view === "certs") loadCertificates();
}

// ---------------------------------------------------------------------------
// Arquivos: escolher e arrastar
// ---------------------------------------------------------------------------

function fileInput(accept, onPick) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = accept;
  input.className = "sr-only";
  // precisa estar no documento: input solto não abre o seletor em todo motor
  document.body.appendChild(input);
  input.addEventListener("change", () => {
    const file = input.files[0];
    input.value = "";
    if (file) onPick(file);
  });
  return input;
}

const pdfInput = fileInput("application/pdf,.pdf", (f) => loadPdf(f));
const validateInput = fileInput("application/pdf,.pdf", (f) => validateFile(f));

el.pickPdf.addEventListener("click", () => askBeforeReplacing(() => pdfInput.click()));
el.changePdf.addEventListener("click", () => askBeforeReplacing(() => pdfInput.click()));

/**
 * Trocar de documento joga fora o que está na tela. Quando há algo a perder —
 * um documento aberto, uma marca posicionada, uma assinatura recém-feita —
 * a troca passa a ser uma escolha, não um acidente.
 */
function askBeforeReplacing(acao) {
  if (!state.file) {
    acao();
    return;
  }
  const assinado = Boolean(state.signed);
  showDialog({
    title: assinado ? "Documento já assinado" : "Trocar de documento",
    text: assinado
      ? "Se ainda não baixou o arquivo assinado, baixe antes de sair daqui. Ele não fica guardado."
      : `"${state.file.name}" está aberto. Abrir outro descarta a marcação feita nele.`,
    actions: [
      { label: assinado ? "Ficar com este" : "Continuar neste", style: "primary" },
      ...(assinado && state.downloadUrl
        ? [{ label: "Baixar assinado", onClick: () => baixar(state.lastSignedName) }]
        : []),
      { label: "Escolher outro", onClick: acao },
    ],
  });
}
el.pickValidate.addEventListener("click", () => validateInput.click());

// Arrastar e soltar não é oferecido na interface: na janela nativa o WebKit
// não entrega os arquivos ao DOM, e prometer o que não funciona é pior do que
// não prometer. O que fica é o cancelamento do comportamento padrão — sem ele,
// um arquivo solto por engano faz a janela navegar para fora do app. Quando o
// evento traz arquivo (navegador comum), ele é aproveitado.
window.addEventListener("dragover", (e) => {
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
});

window.addEventListener("drop", (e) => {
  e.preventDefault();
  const file = e.dataTransfer?.files?.[0];
  if (!file) return;
  if (state.view === "validate") validateFile(file);
  else askBeforeReplacing(() => loadPdf(file));
});

// ---------------------------------------------------------------------------
// Carregar o PDF
// ---------------------------------------------------------------------------

async function loadPdf(file) {
  let bytes;
  try {
    bytes = new Uint8Array(await file.arrayBuffer());
  } catch (err) {
    showError({
      title: "Não consegui ler o arquivo",
      error: "O arquivo não pôde ser lido do disco.",
      detail: describe(err),
    });
    return;
  }

  if (!looksLikePdf(bytes)) {
    showError({
      title: "Isso não é um PDF",
      error: `"${file.name}" não é um documento PDF. Escolha um arquivo .pdf para assinar.`,
      detail: `começa com ${firstBytes(bytes)}`,
    });
    return;
  }

  let doc;
  try {
    doc = await pdfjsLib.getDocument({ data: bytes, isEvalSupported: false }).promise;
  } catch (err) {
    console.error("pdf.js não abriu o documento:", err);
    showError(pdfjsError(err, file.name));
    return;
  }

  state.file = file;
  state.doc = doc;
  state.page = 1;
  state.pageCount = doc.numPages;
  state.marks = [];
  state.zoom = null;
  state.thumbs.clear();

  el.dropzone.hidden = true;
  el.stage.hidden = false;
  el.pager.hidden = state.pageCount < 2;
  el.docbar.hidden = false;
  el.zoom.hidden = false;
  el.pages.hidden = state.pageCount < 2;

  el.pdfName.textContent = file.name;
  el.pageTotal.textContent = String(state.pageCount);
  el.pagerTotal.textContent = String(state.pageCount);

  resetSpot();
  await renderPage();
  buildThumbs();
  updateSubmitState();
}

/** Magic bytes: `%PDF-` pode vir depois de algum lixo no começo do arquivo. */
function looksLikePdf(bytes) {
  return new TextDecoder("latin1").decode(bytes.subarray(0, 1024)).includes("%PDF-");
}

function firstBytes(bytes) {
  return Array.from(bytes.subarray(0, 4))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join(" ");
}

/** Traduz a exceção do pdf.js para algo em que o usuário possa agir. */
function pdfjsError(err, nome) {
  const tipo = err?.name || "";
  if (tipo === "PasswordException") {
    return {
      title: "PDF protegido por senha",
      error: `"${nome}" está protegido. Remova a proteção antes de assinar.`,
      code: "PDF_ENCRYPTED",
    };
  }
  if (tipo === "InvalidPDFException") {
    return {
      title: "PDF corrompido",
      error: `"${nome}" está incompleto ou danificado.`,
      detail: describe(err),
    };
  }
  return {
    title: "Não consegui abrir este PDF",
    error: "O arquivo não pôde ser lido como PDF.",
    detail: describe(err),
  };
}

function describe(err) {
  if (!err) return "";
  return `${err.name || "Erro"}: ${String(err.message || err).slice(0, 160)}`;
}

// ---------------------------------------------------------------------------
// Renderização
// ---------------------------------------------------------------------------

function fitScale(page) {
  const base = page.getViewport({ scale: 1 });
  const box = el.stage.getBoundingClientRect();
  const folga = 44;
  return Math.max(
    0.15,
    Math.min((box.width - folga) / base.width, (box.height - folga) / base.height, 4),
  );
}

async function renderPage() {
  if (!state.doc) return;
  const page = await state.doc.getPage(state.page);
  const escala = state.zoom ?? fitScale(page);
  const viewport = page.getViewport({ scale: escala });
  state.viewport = viewport;

  // Duas medidas: o backing store (pixels reais × devicePixelRatio, para não
  // borrar em tela HiDPI) e o tamanho CSS.
  const dpr = window.devicePixelRatio || 1;
  el.canvas.width = Math.floor(viewport.width * dpr);
  el.canvas.height = Math.floor(viewport.height * dpr);
  el.canvas.style.width = `${viewport.width}px`;
  el.canvas.style.height = `${viewport.height}px`;
  el.wrap.style.width = `${viewport.width}px`;
  el.wrap.style.height = `${viewport.height}px`;

  state.renderTask?.cancel();
  const ctx = el.canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  state.renderTask = page.render({ canvasContext: ctx, viewport });
  try {
    await state.renderTask.promise;
  } catch (err) {
    if (err?.name !== "RenderingCancelledException") throw err;
  }

  el.pageNum.textContent = String(state.page);
  el.pagerNum.textContent = String(state.page);
  el.prev.disabled = state.page <= 1;
  el.next.disabled = state.page >= state.pageCount;
  el.zoomLevel.textContent = state.zoom === null ? "ajustar" : `${Math.round(escala * 100)}%`;
  markCurrentThumb();
  drawMark();
}

el.prev.addEventListener("click", () => gotoPage(state.page - 1));
el.next.addEventListener("click", () => gotoPage(state.page + 1));

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea") || el.dialog.open) return;
  if (state.view !== "sign") return;
  if (e.key === "ArrowLeft") gotoPage(state.page - 1);
  if (e.key === "ArrowRight") gotoPage(state.page + 1);
});

async function gotoPage(n) {
  if (!state.doc || n < 1 || n > state.pageCount || n === state.page) return;
  state.page = n;
  await renderPage();
}

el.zoomIn.addEventListener("click", () => setZoom(+1));
el.zoomOut.addEventListener("click", () => setZoom(-1));

async function setZoom(direcao) {
  if (!state.doc) return;
  const page = await state.doc.getPage(state.page);
  const atual = state.zoom ?? fitScale(page);
  state.zoom = Math.max(0.25, Math.min(Math.round((atual + direcao * 0.2) * 20) / 20, 4));
  await renderPage();
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  // A marca sobrevive: ela está em pontos PDF, não em pixels.
  resizeTimer = setTimeout(() => renderPage(), 120);
});

// ---------------------------------------------------------------------------
// Miniaturas
// ---------------------------------------------------------------------------

function buildThumbs() {
  el.pagesList.innerHTML = "";
  if (state.pageCount < 2) return;

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        renderThumb(Number(entry.target.dataset.page));
        observer.unobserve(entry.target);
      }
    },
    { root: el.pages, rootMargin: "220px" },
  );

  for (let n = 1; n <= state.pageCount; n++) {
    const li = document.createElement("li");
    const botao = document.createElement("button");
    botao.type = "button";
    botao.className = "thumb";
    botao.dataset.page = String(n);
    botao.setAttribute("aria-label", `Ir para a página ${n}`);
    botao.innerHTML = `<div class="thumb__skeleton"></div><span class="thumb__n">${n}</span>`;
    botao.addEventListener("click", () => gotoPage(n));
    li.appendChild(botao);
    el.pagesList.appendChild(li);
    state.thumbs.set(n, botao);
    observer.observe(botao);
  }
  markCurrentThumb();
}

async function renderThumb(n) {
  const botao = state.thumbs.get(n);
  if (!botao || botao.dataset.rendered === "1") return;
  botao.dataset.rendered = "1";

  const page = await state.doc.getPage(n);
  const base = page.getViewport({ scale: 1 });
  const viewport = page.getViewport({ scale: 80 / base.width });

  const canvas = document.createElement("canvas");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(viewport.width * dpr);
  canvas.height = Math.floor(viewport.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  await page.render({ canvasContext: ctx, viewport }).promise;
  botao.querySelector(".thumb__skeleton")?.replaceWith(canvas);
}

function markCurrentThumb() {
  const marcadas = new Set(state.marks.map((m) => m.page));
  for (const [n, botao] of state.thumbs) {
    botao.setAttribute("aria-current", n === state.page ? "true" : "false");
    botao.dataset.signed = marcadas.has(n) ? "true" : "false";
  }
}

// ---------------------------------------------------------------------------
// A marca: posicionar (o tamanho é do app)
// ---------------------------------------------------------------------------

function pointerToViewport(e) {
  return clientToViewport(e.clientX, e.clientY, el.canvas.getBoundingClientRect(), state.viewport);
}

/** Razão entre pixels CSS e unidades de viewport (zoom do navegador etc.). */
function cssRatio() {
  const rect = el.canvas.getBoundingClientRect();
  return rect.width / state.viewport.width || 1;
}

/** A marca desta página, se houver. */
function markOfPage(n = state.page) {
  return state.marks.find((m) => m.page === n) || null;
}

/**
 * Põe a assinatura com o centro no ponto dado, sem deixar sair da página.
 *
 * Uma marca por página: clicar de novo na mesma página move a que já está lá;
 * clicar em outra página acrescenta mais uma. É assim que se assina em várias
 * páginas — e cada marca vira uma assinatura de verdade no documento.
 */
function placeMarkAt(pontoViewport) {
  const { width, height } = state.markSize;
  const centro = viewportToPdfPoint(pontoViewport.x, pontoViewport.y, state.viewport);

  // limites da página em pontos PDF, tirados dos cantos do viewport
  const a = viewportToPdfPoint(0, 0, state.viewport);
  const b = viewportToPdfPoint(state.viewport.width, state.viewport.height, state.viewport);
  const minX = Math.min(a.x, b.x);
  const maxX = Math.max(a.x, b.x);
  const minY = Math.min(a.y, b.y);
  const maxY = Math.max(a.y, b.y);

  const x1 = Math.min(Math.max(centro.x - width / 2, minX), maxX - width);
  const y1 = Math.min(Math.max(centro.y - height / 2, minY), maxY - height);
  const box = { x1, y1, x2: x1 + width, y2: y1 + height };

  const existente = markOfPage();
  if (existente) existente.box = box;
  else state.marks.push({ page: state.page, box });

  drawMark();
  markCurrentThumb();
  describeMarks();
  clearFieldError("box");
  updateSubmitState();
}

/** Tira a assinatura de uma página. */
function removeMark(n = state.page) {
  state.marks = state.marks.filter((m) => m.page !== n);
  drawMark();
  markCurrentThumb();
  describeMarks();
  updateSubmitState();
}

/** O painel diz quantas assinaturas vão sair e em que páginas. */
function describeMarks() {
  const paginas = state.marks.map((m) => m.page).sort((x, y) => x - y);
  el.clear.disabled = paginas.length === 0;
  el.clear.textContent = paginas.length > 1 ? "Tirar todas" : "Tirar do documento";

  if (!paginas.length) {
    el.spot.dataset.set = "false";
    el.spotText.textContent = "Clique na página para posicionar a assinatura.";
    return;
  }
  el.spot.dataset.set = "true";
  const lista = paginas.join(", ");
  el.spotText.innerHTML =
    paginas.length === 1
      ? `<b>Página ${lista}</b>`
      : `<b>${paginas.length} assinaturas</b>, nas páginas <span class="pt">${lista}</span>`;
}

let arrastando = null;

el.canvas.addEventListener("pointerdown", (e) => {
  if (!state.viewport || state.busy) return;
  placeMarkAt(pointerToViewport(e));
  arrastando = { origem: true };
  try {
    el.canvas.setPointerCapture(e.pointerId);
  } catch {
    /* evento sintético: segue sem captura */
  }
});

el.canvas.addEventListener("pointermove", (e) => {
  if (arrastando) placeMarkAt(pointerToViewport(e));
});

el.canvas.addEventListener("pointerup", () => {
  arrastando = null;
});

// arrastar a marca já posicionada
el.mark.addEventListener("pointerdown", (e) => {
  if (!markOfPage() || state.busy) return;
  e.stopPropagation();
  el.mark.classList.add("is-dragging");
  arrastando = { movendo: true };
  try {
    el.mark.setPointerCapture(e.pointerId);
  } catch {
    /* idem */
  }
});

el.mark.addEventListener("pointermove", (e) => {
  if (arrastando?.movendo) placeMarkAt(pointerToViewport(e));
});

for (const evento of ["pointerup", "pointercancel"]) {
  el.mark.addEventListener(evento, () => {
    arrastando = null;
    el.mark.classList.remove("is-dragging");
  });
}

/** Desenha a marca desta página e a prévia do carimbo. */
function drawMark() {
  const marca = markOfPage();
  if (!marca || !state.viewport || state.signed) {
    el.mark.hidden = true;
    return;
  }
  const rect = pdfBoxToViewportRect(marca.box, state.viewport);
  const r = cssRatio();
  el.mark.hidden = false;
  el.mark.style.left = `${rect.x * r}px`;
  el.mark.style.top = `${rect.y * r}px`;
  el.mark.style.width = `${rect.width * r}px`;
  el.mark.style.height = `${rect.height * r}px`;
  // a prévia acompanha o zoom: 1 pt do PDF = `escala` pixels na tela
  const escala = (rect.height * r) / state.markSize.height;
  el.mark.style.fontSize = `${Math.max(4, 10 * escala)}px`;
  updatePreview();
}

/** A prévia mostra o que o carimbo vai dizer, com os dados que já temos. */
function updatePreview() {
  const cert = state.certificates.find((c) => c.id === state.chosenCert);
  el.markName.textContent = cert ? cert.holder : "seu nome";
  el.markDoc.textContent = cert?.document || "CPF";
  el.markDoc.hidden = !cert?.document && Boolean(cert);
  el.markDate.textContent = agoraFormatado();

  el.markReason.hidden = !el.reason.value.trim();
  el.markReason.textContent = `Motivo: ${el.reason.value.trim()}`;
  el.markLocation.hidden = !el.location.value.trim();
  el.markLocation.textContent = `Local: ${el.location.value.trim()}`;
}

function agoraFormatado() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} às ${p(d.getHours())}:${p(d.getMinutes())}`;
}

el.clear.addEventListener("click", () => {
  state.marks = [];
  el.mark.hidden = true;
  markCurrentThumb();
  describeMarks();
  updateSubmitState();
});

// Botão direito sobre a assinatura tira ela da página.
el.mark.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (state.signed) return;
  removeMark();
});

function resetSpot() {
  state.marks = [];
  describeMarks();
}

// ---------------------------------------------------------------------------
// Certificados
// ---------------------------------------------------------------------------

async function loadCertificates() {
  try {
    const resposta = await fetch("/api/certificates");
    state.certificates = await resposta.json();
  } catch {
    state.certificates = [];
  }
  renderCertChoices();
  renderCertList();
}

/** As opções no painel de assinar: os do cofre, mais "usar outro arquivo". */
function renderCertChoices() {
  const validos = state.certificates.filter((c) => !c.expired);
  el.certChoices.innerHTML = "";

  for (const cert of validos) {
    el.certChoices.appendChild(
      certOption(
        cert.id,
        cert.holder,
        `${cert.document || "sem CPF"} · ${
          cert.has_password ? "senha guardada" : "pede a senha"
        }`,
      ),
    );
  }
  el.certChoices.appendChild(
    certOption("upload", "Usar outro arquivo", "escolher um .pfx desta vez"),
  );

  if (!validos.some((c) => c.id === state.chosenCert)) {
    state.chosenCert = validos.length ? validos[0].id : "upload";
  }
  const marcado = el.certChoices.querySelector(`input[value="${state.chosenCert}"]`);
  if (marcado) marcado.checked = true;
  el.certUpload.hidden = state.chosenCert !== "upload";
  updatePreview();
}

function certOption(valor, titulo, meta) {
  const label = document.createElement("label");
  label.className = "certopt";
  label.innerHTML = `
    <input type="radio" name="cert" value="${escapeHtml(valor)}" />
    <span class="certopt__info">
      <span class="certopt__name">${escapeHtml(titulo)}</span>
      <span class="certopt__meta">${escapeHtml(meta)}</span>
    </span>`;
  label.querySelector("input").addEventListener("change", () => {
    state.chosenCert = valor;
    el.certUpload.hidden = valor !== "upload";
    clearFieldError("pfx");
    updatePreview();
    updateSubmitState();
  });
  return label;
}

/** A lista da tela "Certificados": o que cada um é, e o que dá para fazer. */
function renderCertList() {
  el.certlist.innerHTML = "";
  if (!state.certificates.length) {
    const vazio = document.createElement("li");
    vazio.className = "certlist__empty";
    vazio.textContent = "Nenhum certificado guardado ainda.";
    el.certlist.appendChild(vazio);
    return;
  }

  for (const cert of state.certificates) {
    const li = document.createElement("li");
    li.className = `certcard${cert.expired ? " is-expired" : ""}`;
    li.innerHTML = `
      <div class="certcard__info">
        <span class="certcard__name">${escapeHtml(cert.holder)}</span>
        <span class="certcard__meta">${escapeHtml(cert.document || "sem CPF")} · ${
          cert.expired ? "venceu" : "vence"
        } <b>${formatDate(cert.valid_until)}</b></span>
        <span class="certcard__pw ${cert.has_password ? "is-stored" : ""}">${
          cert.has_password ? "senha guardada" : "pede a senha ao assinar"
        }</span>
      </div>`;

    const acoes = document.createElement("div");
    acoes.className = "certcard__actions";

    if (cert.has_password) {
      const esquecer = document.createElement("button");
      esquecer.type = "button";
      esquecer.className = "quiet-btn";
      esquecer.textContent = "Apagar senha";
      esquecer.addEventListener("click", () => confirmForgetPassword(cert));
      acoes.appendChild(esquecer);
    }

    const excluir = document.createElement("button");
    excluir.type = "button";
    excluir.className = "danger-btn";
    excluir.textContent = "Excluir";
    excluir.addEventListener("click", () => confirmDelete(cert));
    acoes.appendChild(excluir);

    li.appendChild(acoes);
    el.certlist.appendChild(li);
  }
}

function confirmForgetPassword(cert) {
  showDialog({
    title: "Apagar a senha guardada",
    text: `O certificado de ${cert.holder} continua no cofre. A senha some desta máquina, e o app volta a pedi-la a cada assinatura.`,
    actions: [
      { label: "Deixar como está" },
      {
        label: "Apagar senha",
        style: "primary",
        onClick: async () => {
          await fetch(`/api/certificates/${cert.id}/password`, { method: "DELETE" });
          await loadCertificates();
        },
      },
    ],
  });
}

function confirmDelete(cert) {
  showDialog({
    kind: "error",
    title: "Excluir certificado",
    text: `${cert.holder} sai do cofre desta máquina, com a senha se houver. O arquivo original continua onde você guardou, e dá para cadastrar de novo depois.`,
    actions: [
      { label: "Cancelar" },
      {
        label: "Excluir",
        style: "primary",
        onClick: async () => {
          await fetch(`/api/certificates/${cert.id}`, { method: "DELETE" });
          await loadCertificates();
        },
      },
    ],
  });
}

function formatDate(iso) {
  const [ano, mes, dia] = String(iso).split("-");
  return `${dia}/${mes}/${ano}`;
}

// upload no painel de assinar
el.pfx.addEventListener("change", () => {
  const file = el.pfx.files[0];
  el.pfxName.textContent = file ? file.name : "Escolher arquivo .pfx ou .p12";
  el.pfxPick.classList.toggle("is-set", Boolean(file));
  clearFieldError("pfx");
  updateSubmitState();
});

function togglePassword(input, botao) {
  const vendo = input.type === "text";
  input.type = vendo ? "password" : "text";
  botao.textContent = vendo ? "ver" : "ocultar";
  botao.setAttribute("aria-label", vendo ? "Mostrar senha" : "Ocultar senha");
}

// cadastro na tela de certificados
el.newPfx.addEventListener("change", () => {
  const file = el.newPfx.files[0];
  el.newPfxName.textContent = file ? file.name : "Escolher arquivo .pfx ou .p12";
  el.newPfxPick.classList.toggle("is-set", Boolean(file));
  updateAddCertState();
});

el.newPassword.addEventListener("input", updateAddCertState);
el.newStorePassword.addEventListener("change", updateAddCertState);
el.newRiskOk.addEventListener("change", updateAddCertState);

/** Guardar a senha exige marcar que entendeu o que isso significa. */
function updateAddCertState() {
  const guardarSenha = el.newStorePassword.checked;
  el.newRisk.hidden = !guardarSenha;
  if (!guardarSenha) el.newRiskOk.checked = false;

  const pronto =
    Boolean(el.newPfx.files[0]) &&
    Boolean(el.newPassword.value) &&
    (!guardarSenha || el.newRiskOk.checked);
  el.addCertBtn.disabled = !pronto;
}

el.newToggle.addEventListener("click", () => togglePassword(el.newPassword, el.newToggle));

el.addcert.addEventListener("submit", async (e) => {
  e.preventDefault();
  const dados = new FormData();
  dados.append("pfx", el.newPfx.files[0]);
  dados.append("password", el.newPassword.value);
  dados.append("store_password", el.newStorePassword.checked ? "true" : "false");

  el.addCertBtn.disabled = true;
  try {
    const resposta = await fetch("/api/certificates", { method: "POST", body: dados });
    const corpo = await resposta.json();
    if (!resposta.ok) {
      showError({ title: "Certificado não foi guardado", ...corpo });
      return;
    }
    el.newPassword.value = "";
    el.newPfx.value = "";
    el.newPfxName.textContent = "Escolher arquivo .pfx ou .p12";
    el.newPfxPick.classList.remove("is-set");
    el.newStorePassword.checked = false;
    el.newRiskOk.checked = false;
    el.newRisk.hidden = true;
    await loadCertificates();
    showDialog({
      kind: "done",
      title: "Certificado guardado",
      text: corpo.has_password
        ? `${corpo.holder} já aparece na hora de assinar, com a senha junto.`
        : `${corpo.holder} já aparece na hora de assinar. A senha é pedida na hora.`,
      actions: [{ label: "Pronto", style: "primary" }],
    });
  } finally {
    updateAddCertState();
  }
});

// ---------------------------------------------------------------------------
// Estado do formulário
// ---------------------------------------------------------------------------

for (const campo of [el.reason, el.location]) {
  campo.addEventListener("input", updatePreview);
}

/** A instrução do painel sempre aponta o próximo passo que falta. */
function updateSubmitState() {
  // Documento assinado é ponto final: não há mais nada a preencher, e o botão
  // deixa de ser "assinar" para ser a saída.
  if (state.signed) {
    setPanelEnabled(false);
    el.signedNote.hidden = false;
    el.submit.disabled = false;
    el.submit.classList.remove("is-busy");
    el.submitLabel.textContent = "Novo documento";
    el.guide.dataset.state = "ready";
    el.guide.textContent = `Assinado. O arquivo é ${state.lastSignedName}`;
    return;
  }
  el.signedNote.hidden = true;
  el.submitLabel.textContent = state.busy ? "Assinando" : "Assinar documento";

  const temPdf = Boolean(state.file);
  const temMarca = state.marks.length > 0;
  const usandoCofre = state.chosenCert && state.chosenCert !== "upload";
  const temCert = usandoCofre || Boolean(el.pfx.files[0]);
  const pronto = temPdf && temMarca && temCert;

  setPanelEnabled(temPdf);
  el.submit.disabled = !pronto || state.busy;

  const [estado, texto] = !temPdf
    ? ["pdf", "Escolha o documento para começar."]
    : !temMarca
      ? ["box", "Clique na página, onde a assinatura deve entrar."]
      : !temCert
        ? ["cert", "Agora escolha o certificado."]
        : [
            "ready",
            state.marks.length > 1
              ? `Tudo pronto. Saem ${state.marks.length} assinaturas, uma por página marcada.`
              : "Tudo pronto. A assinatura entra onde você marcou.",
          ];

  el.guide.dataset.state = estado;
  el.guide.textContent = texto;
}

/** Sem documento aberto não há o que preencher: o painel fica apagado. */
function setPanelEnabled(ligado) {
  for (const bloco of document.querySelectorAll(".panel .block")) {
    bloco.classList.toggle("is-off", !ligado);
    for (const controle of bloco.querySelectorAll("input, button")) {
      if (controle === el.clear) continue; // tem regra própria
      controle.disabled = !ligado;
    }
  }
  if (!ligado) el.clear.disabled = true;
}

// ---------------------------------------------------------------------------
// Assinar
// ---------------------------------------------------------------------------

el.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.busy || el.submit.disabled) return;

  if (state.signed) {
    askBeforeReplacing(startOver);
    return;
  }

  const guardado = state.certificates.find((c) => c.id === state.chosenCert);
  const novoArquivo = !guardado;

  // A senha é pedida aqui, no meio da tela, e só quando falta: certificado
  // guardado com senha vai direto para o trabalho.
  let senha = null;
  let guardarSenha = false;

  if (novoArquivo || !guardado.has_password) {
    const resposta = await askPassword({
      titulo: "Senha do certificado",
      texto: novoArquivo
        ? el.pfx.files[0]?.name || "Digite a senha para assinar."
        : `${guardado.holder}. Digite a senha para assinar.`,
      // guardar a senha só faz sentido se o certificado vai ficar no cofre
      offerRemember: novoArquivo ? el.remember.checked : true,
    });
    if (resposta === null) return;
    senha = resposta.senha;
    guardarSenha = resposta.guardar;
  }

  dialogWorking(
    state.marks.length > 1 ? `Assinando ${state.marks.length} páginas` : "Assinando",
    "Isso leva alguns segundos.",
  );
  setBusy(true);

  const dados = new FormData();
  dados.append("pdf", state.file);
  // uma entrada por página marcada; o servidor assina uma sobre a outra
  dados.append(
    "marks",
    JSON.stringify(
      state.marks
        .slice()
        .sort((a, b) => a.page - b.page)
        .map((m) => ({ page: m.page, ...m.box })),
    ),
  );
  dados.append("reason", el.reason.value);
  dados.append("location", el.location.value);
  dados.append("timestamp", el.timestamp.checked ? "true" : "false");
  if (senha !== null) dados.append("password", senha);
  dados.append("remember_password", guardarSenha ? "true" : "false");

  if (!novoArquivo) {
    dados.append("certificate_id", state.chosenCert);
  } else {
    dados.append("pfx", el.pfx.files[0]);
    dados.append("remember", el.remember.checked ? "true" : "false");
  }

  try {
    const resposta = await fetch("/api/sign", { method: "POST", body: dados });
    if (!resposta.ok) {
      const payload = await resposta.json().catch(() => ({
        error: "Falha inesperada ao assinar.",
        code: "UNKNOWN",
      }));
      showError(payload);
      return;
    }
    if (el.remember.checked || guardarSenha) await loadCertificates();
    await signedReady(await resposta.blob(), resposta.headers);
  } catch (err) {
    showError({
      title: "Servidor local não respondeu",
      error: "O app não conseguiu falar com o próprio servidor.",
      detail: describe(err),
    });
  } finally {
    senha = null;
    setBusy(false);
  }
});

function setBusy(ocupado) {
  state.busy = ocupado;
  el.submit.classList.toggle("is-busy", ocupado);
  if (ocupado) el.submitLabel.textContent = "Assinando";
  el.submit.disabled = true;
  if (!ocupado) updateSubmitState();
}

/** Assinatura pronta: o diálogo conduz o que fazer com o arquivo. */
async function signedReady(blob, headers) {
  const nome = signedFilename(headers.get("content-disposition"));
  if (state.downloadUrl) URL.revokeObjectURL(state.downloadUrl);
  state.downloadUrl = URL.createObjectURL(blob);
  state.lastSignedName = nome;
  state.signed = { nome };

  // O documento na tela passa a ser o assinado: some a prévia e aparece o
  // carimbo de verdade, no lugar em que ele de fato ficou.
  await showSignedDocument(blob);

  const quantas = Number(headers.get("X-Signatures-Added") || 1);
  const avisos = [];
  avisos.push(
    quantas > 1
      ? `Saíram ${quantas} assinaturas, uma em cada página marcada.`
      : "A assinatura entrou onde você marcou.",
  );
  if (headers.get("X-Already-Signed") === "1") {
    avisos.push("O documento já tinha assinatura de outra pessoa; a sua foi somada às anteriores.");
  }
  if (headers.get("X-Timestamped") === "1") avisos.push("Com carimbo do tempo.");

  showDialog({
    kind: "done",
    title: quantas > 1 ? "Documento assinado" : "Documento assinado",
    text: avisos.join(" "),
    note: nome,
    actions: [
      { label: "Baixar", style: "primary", onClick: () => baixar(nome) },
      { label: "Assinar outro", onClick: startOver },
    ],
  });
}

/** Recarrega o visualizador com o PDF assinado e tranca o painel. */
async function showSignedDocument(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const paginaAtual = state.page;
  try {
    state.doc?.destroy?.();
    state.doc = await pdfjsLib.getDocument({ data: bytes, isEvalSupported: false }).promise;
    state.page = Math.min(paginaAtual, state.doc.numPages);
    state.thumbs.clear();
    await renderPage();
    buildThumbs();
  } catch (err) {
    console.error("não consegui reabrir o documento assinado:", err);
  }
  el.app.dataset.signed = "true";
  el.mark.hidden = true;
  updateSubmitState();
}

function baixar(nome) {
  // O navegador não avisa quando o download termina. O loader cobre o tempo
  // entre o clique e o arquivo existir na pasta, que é curto mas não é zero.
  showDialog({
    kind: "done",
    title: "Baixando",
    text: "Salvando na sua pasta de downloads.",
    working: true,
    actions: [],
  });

  const link = document.createElement("a");
  link.href = state.downloadUrl;
  link.download = nome;
  document.body.appendChild(link);
  link.click();
  link.remove();

  setTimeout(() => downloadedDialog(nome), 900);
}

function downloadedDialog(nome) {
  showDialog({
    kind: "done",
    title: "Arquivo baixado",
    text: "Está na sua pasta de downloads.",
    note: nome,
    actions: [
      { label: "Abrir", style: "primary", onClick: () => reveal(nome, "file") },
      { label: "Ver na pasta", onClick: () => reveal(nome, "folder") },
      { label: "Baixar de novo", onClick: () => baixar(nome) },
      { label: "Novo documento", onClick: startOver },
    ],
  });
}

/** Pede ao servidor local para abrir o arquivo (ou a pasta) no desktop. */
async function reveal(nome, what) {
  const dados = new FormData();
  dados.append("filename", nome);
  dados.append("what", what);
  try {
    const resposta = await fetch("/api/reveal", { method: "POST", body: dados });
    const corpo = await resposta.json();
    if (!corpo.opened) {
      showDialog({
        title: "Não achei o arquivo",
        text: `Procurei em ${corpo.folder || "sua pasta de downloads"}. O download pode ter ido para outro lugar, ou ainda não terminou.`,
        note: nome,
        actions: [
          { label: "Baixar de novo", style: "primary", onClick: () => baixar(nome) },
          { label: "Fechar" },
        ],
      });
    }
  } catch (err) {
    showError({ title: "Não consegui abrir", error: describe(err) });
  }
}

/** Recomeça do documento — o certificado escolhido continua escolhido. */
function startOver() {
  if (state.downloadUrl) {
    URL.revokeObjectURL(state.downloadUrl);
    state.downloadUrl = null;
  }
  state.doc?.destroy?.();
  Object.assign(state, {
    doc: null, file: null, marks: [], viewport: null,
    page: 1, pageCount: 0, zoom: null, signed: null, lastSignedName: null,
  });
  delete el.app.dataset.signed;
  state.thumbs.clear();

  el.pagesList.innerHTML = "";
  el.mark.hidden = true;
  el.stage.hidden = true;
  el.pager.hidden = true;
  el.pages.hidden = true;
  el.docbar.hidden = true;
  el.zoom.hidden = true;
  el.dropzone.hidden = false;

  goTo("sign");
  resetSpot();
  clearFieldErrors();
  updateSubmitState();
  el.stage.scrollTop = 0;
}

function signedFilename(disposition) {
  const achado = /filename="([^"]+)"/.exec(disposition || "");
  if (achado) return achado[1];
  const base = (state.file?.name || "documento.pdf").replace(/\.pdf$/i, "");
  return `${base}-assinado.pdf`;
}

// ---------------------------------------------------------------------------
// Validar
// ---------------------------------------------------------------------------

let validando = false;

/**
 * Confere as assinaturas de um arquivo.
 *
 * Enquanto trabalha, a porta de entrada some: validar é rápido, mas não é
 * instantâneo, e abrir outro arquivo no meio deixaria dois relatórios
 * disputando a mesma tela.
 */
async function validateFile(file) {
  if (validando) return;
  validando = true;
  goTo("validate");
  el.validateDrop.hidden = true;
  el.validateResult.innerHTML = `
    <div class="working">
      <span class="working__spin" aria-hidden="true"></span>
      <span>Conferindo <b>${escapeHtml(file.name)}</b>…</span>
    </div>`;

  const dados = new FormData();
  dados.append("pdf", file);
  try {
    const resposta = await fetch("/api/validate", { method: "POST", body: dados });
    const corpo = await resposta.json();
    if (!resposta.ok) {
      el.validateResult.innerHTML = "";
      showError({ title: "Não consegui validar", ...corpo });
      return;
    }
    renderReport(corpo);
  } catch (err) {
    el.validateResult.innerHTML = "";
    showError({ title: "Não consegui validar", error: describe(err) });
  } finally {
    validando = false;
    el.validateDrop.hidden = false;
  }
}

function renderReport({ filename, signatures }) {
  if (!signatures.length) {
    el.validateResult.innerHTML = `
      <div class="sigcard is-bad">
        <div class="sigcard__head"><span class="sigcard__holder">Documento sem assinatura</span></div>
        <p style="margin:0;color:var(--text-dim)">${escapeHtml(filename)} não tem nenhuma assinatura digital.</p>
      </div>`;
    return;
  }

  const cartoes = signatures
    .map((s) => {
      const carimbo = s.kind === "timestamp";
      const quando = s.signed_at || s.timestamped_at;
      return `
      <div class="sigcard${s.ok ? "" : " is-bad"}">
        <div class="sigcard__head">
          <span class="sigcard__holder">${escapeHtml(s.holder)}</span>
          <span class="sigcard__kind">${carimbo ? "carimbo do tempo" : "assinatura"}</span>
        </div>
        <dl class="sigcard__grid">
          ${s.document ? `<dt>documento</dt><dd>${escapeHtml(s.document)}</dd>` : ""}
          <dt>íntegro</dt><dd class="${s.intact ? "badge-ok" : "badge-bad"}">${s.intact ? "sim" : "não"}</dd>
          <dt>confiável</dt><dd class="${s.trusted ? "badge-ok" : "badge-bad"}">${s.trusted ? "sim" : "não"}</dd>
          <dt>abrange</dt><dd>${escapeHtml(s.coverage_label || s.coverage)}</dd>
          ${quando ? `<dt>data</dt><dd>${escapeHtml(formatDateTime(quando))}</dd>` : ""}
          ${!carimbo && s.timestamped_at ? `<dt>carimbo</dt><dd class="badge-ok">${escapeHtml(formatDateTime(s.timestamped_at))}</dd>` : ""}
        </dl>
        ${(s.problems || [])
          .map((p) => `<p class="sigcard__problem">${escapeHtml(p)}</p>`)
          .join("")}
      </div>`;
    })
    .join("");

  const todasOk = signatures.every((s) => s.ok);
  el.validateResult.innerHTML = `
    <div class="report">
      <p class="report__file">${escapeHtml(filename)} · ${signatures.length} campo(s) ·
        <span class="${todasOk ? "badge-ok" : "badge-bad"}">${
          todasOk ? "tudo íntegro e confiável" : "há algo para olhar"
        }</span></p>
      ${cartoes}
    </div>`;
}

function formatDateTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} às ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// ---------------------------------------------------------------------------
// Apoio
// ---------------------------------------------------------------------------

function clearFieldErrors() {
  for (const node of document.querySelectorAll("[data-field].has-error")) {
    node.classList.remove("has-error");
  }
}

function clearFieldError(nome) {
  document.querySelector(`[data-field="${nome}"]`)?.classList.remove("has-error");
}

function escapeHtml(valor) {
  return String(valor).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

// ---------------------------------------------------------------------------
// Início
// ---------------------------------------------------------------------------

(async function start() {
  try {
    const saude = await (await fetch("/api/health")).json();
    if (saude.signature_size) state.markSize = saude.signature_size;
    // sem uma ACT da ICP-Brasil, o carimbo atrapalha em vez de ajudar
    el.tsaWarn.hidden = Boolean(saude.tsa_is_icp);
  } catch {
    /* fica com o tamanho padrão */
  }
  await loadCertificates();
  updateSubmitState();
})();

// A consequência inteira do carimbo fora da ICP-Brasil é longa demais para
// ficar no painel. Fica a uma pergunta de distância.
$("tsa-more")?.addEventListener("click", () => {
  showDialog({
    title: "Por que o carimbo pode atrapalhar",
    text:
      "No Brasil, carimbo do tempo só tem valor se vier de uma autoridade credenciada na " +
      "ICP-Brasil. As brasileiras são pagas e exigem contrato. A que está configurada aqui " +
      "não é credenciada, e o validador oficial reprova o carimbo. Pior: sem uma data " +
      "aceita, ele deixa de confirmar todas as assinaturas do documento, até as de outras " +
      "pessoas que já estavam lá.",
    actions: [{ label: "Entendi", style: "primary" }],
  });
});
