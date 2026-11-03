function readBookmarks() {
  try {
    const saved = JSON.parse(localStorage.getItem("paperpal.bookmarks") || "[]");
    return new Set(Array.isArray(saved) ? saved : []);
  } catch {
    return new Set();
  }
}

const state = {
  bank: null,
  archive: null,
  subjectId: null,
  chapterId: "all",
  type: "all",
  year: "all",
  search: "",
  bookmarkedOnly: false,
  paperId: null,
  shuffleOrder: null,
  bookmarks: readBookmarks(),
  answered: new Map(),
  revealed: new Set(),
  paperSubject: "all",
  paperSubjectSearch: "",
  paperSearch: "",
  paperPage: 1,
  subjectSearch: "",
  practice: null,
  questionCache: new Map(),
  paperCache: new Map(),
  questionPromises: new Map(),
  loadedSubjectId: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value = "") =>
  String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
const semesterLabel = (item) => {
  const semesters = item?.semesters || (item?.semester ? [item.semester] : []);
  return semesters.length ? `Sem ${semesters.join(", ")}` : "";
};

/* ------------------------------------------------------------- rich text */

const MATH_DELIMITERS = [
  { open: "$$", close: "$$", display: true },
  { open: "\\[", close: "\\]", display: true },
  { open: "\\(", close: "\\)", display: false },
  { open: "$", close: "$", display: false },
];

function isEscaped(text, index) {
  let slashes = 0;
  for (let i = index - 1; i >= 0 && text[i] === "\\"; i -= 1) slashes += 1;
  return slashes % 2 === 1;
}

function findClosingDelimiter(text, close, from) {
  let at = text.indexOf(close, from);
  while (at !== -1 && isEscaped(text, at)) at = text.indexOf(close, at + close.length);
  return at;
}

function renderMath(tex, displayMode) {
  if (!window.katex?.renderToString) return prettifyMath(tex);
  try {
    return window.katex.renderToString(tex, {
      displayMode,
      throwOnError: false,
      strict: false,
      trust: false,
    });
  } catch {
    return prettifyMath(tex);
  }
}

function consumeScriptToken(text, start, kind) {
  const rest = text.slice(start);
  if (kind === "sup") {
    const number = rest.match(/^[+\-−]?[0-9]+(?:\.[0-9]+)?/);
    if (number) return number[0];
    const letter = rest.match(/^[A-Za-z\u0370-\u03ffħ]/);
    return letter ? letter[0] : "";
  }
  const letters = rest.match(/^[A-Za-z0-9\u0370-\u03ffħ]+/);
  return letters ? letters[0] : "";
}

function replaceScripts(html) {
  let out = "";
  let index = 0;
  while (index < html.length) {
    const character = html[index];
    if ((character !== "^" && character !== "_") || isEscaped(html, index)) {
      out += character;
      index += 1;
      continue;
    }

    const next = html[index + 1];
    let content = "";
    let end = -1;

    if (next === "{" || next === "(" || next === "[") {
      const close = next === "{" ? "}" : next === "(" ? ")" : "]";
      let depth = 0;
      for (let cursor = index + 1; cursor < html.length; cursor += 1) {
        if (html[cursor] === next) depth += 1;
        else if (html[cursor] === close) {
          depth -= 1;
          if (depth === 0) {
            end = cursor;
            break;
          }
        }
      }
      if (end !== -1) content = html.slice(index + 2, end);
    } else {
      content = consumeScriptToken(html, index + 1, character === "^" ? "sup" : "sub");
      if (content) end = index + content.length;
    }

    if (!content) {
      out += character;
      index += 1;
      continue;
    }

    const tag = character === "^" ? "sup" : "sub";
    out += `<${tag}>${replaceScripts(content)}</${tag}>`;
    index = end + 1;
  }
  return out;
}

function replaceSqrt(html) {
  let out = "";
  let index = 0;
  while (index < html.length) {
    const at = html.indexOf("sqrt(", index);
    if (at === -1) {
      out += html.slice(index);
      break;
    }
    out += html.slice(index, at);
    let depth = 1;
    let cursor = at + 5;
    while (cursor < html.length && depth > 0) {
      if (html[cursor] === "(") depth += 1;
      else if (html[cursor] === ")") depth -= 1;
      if (depth > 0) cursor += 1;
    }
    if (depth !== 0) {
      out += html.slice(at);
      break;
    }
    out += `√(${html.slice(at + 5, cursor)})`;
    index = cursor + 1;
  }
  return out;
}

/**
 * Escape prose and render LaTeX between $...$, $$...$$, \(...\) or \[...\].
 * Older plain-text notation (x^2, a_1, sqrt(...), ->) is prettified as a
 * fallback so existing question files keep working without a rewrite.
 */
function prettifyMath(text = "") {
  let out = esc(text);
  // PDF text layers commonly flatten scientific notation such as
  // "1.6 x 10 -19". Restore the exponent before the script formatter runs.
  out = out
    .replace(/\b(\d+(?:\.\d+)?)\s*[x×]\s*10\s*(?:\^|\s)*([−-]?\s*\d+)\b/g, "$1 × 10^$2")
    .replace(/(?<![A-Za-z])\b10\s*(?:\^|\s)+([−-]?\s*\d+)\b/g, "10^$1")
    .replace(/(?<=\d)\s*[xX]\s*(?=\d)/g, " × ");
  out = replaceSqrt(out);
  out = replaceScripts(out);
  out = out
    .replace(/->/g, "→")
    .replace(/<-/g, "←")
    .replace(/<=/g, "≤")
    .replace(/>=/g, "≥")
    .replace(/\\\$/g, "$");
  return out;
}

function richText(value = "") {
  const text = String(value);
  let html = "";
  let index = 0;

  while (index < text.length) {
    let opening = null;
    for (const delimiter of MATH_DELIMITERS) {
      const at = text.indexOf(delimiter.open, index);
      if (at === -1 || isEscaped(text, at)) continue;
      if (!opening || at < opening.at || (at === opening.at && delimiter.open.length > opening.delimiter.open.length)) {
        opening = { at, delimiter };
      }
    }

    if (!opening) {
      html += prettifyMath(text.slice(index));
      break;
    }

    html += prettifyMath(text.slice(index, opening.at));
    const contentStart = opening.at + opening.delimiter.open.length;
    const closeAt = findClosingDelimiter(text, opening.delimiter.close, contentStart);
    if (closeAt === -1) {
      html += prettifyMath(text.slice(opening.at));
      break;
    }

    const tex = text.slice(contentStart, closeAt);
    const inlineDollar = opening.delimiter.open === "$";
    if (inlineDollar && (!tex.trim() || /^\s|\s$/.test(tex))) {
      html += prettifyMath(text.slice(opening.at, contentStart));
      index = contentStart;
      continue;
    }

    html += renderMath(tex, opening.delimiter.display);
    index = closeAt + opening.delimiter.close.length;
  }

  return html;
}

/* ---------------------------------------------------------- answer layout */

const ABBREVIATIONS = new Set([
  "approx.", "cf.", "e.g.", "eq.", "eqs.", "fig.", "figs.", "i.e.", "no.",
  "p.", "pp.", "ref.", "refs.", "sec.", "vs.",
]);

function splitAnswer(value = "") {
  const text = String(value).replace(/\r\n?/g, "\n");
  const segments = [];

  // Put lettered / numbered sub-parts on their own lines.
  const marked = text.replace(/(\S)\s+(\((?:[ivx]+|[a-z]|\d+)\))\s+/g, "$1\n$2 ");

  marked.split(/\n+/).forEach((paragraph) => {
    const trimmed = paragraph.trim();
    if (!trimmed) return;

    let start = 0;
    for (let index = 0; index < trimmed.length; index += 1) {
      const character = trimmed[index];
      if (character !== "." && character !== "!" && character !== "?") continue;

      const before = trimmed.slice(start, index);
      const beforeTrimmed = before.trim();
      const after = trimmed[index + 1] || "";

      if (character === ".") {
        // Decimals and leading list markers ("1. ...") are not sentence ends.
        if (/\d/.test(after)) continue;
        if (/^(?:step\s+)?\d{1,2}$/i.test(beforeTrimmed)) continue;
        const token = (before + character).match(/([A-Za-z.]+)$/);
        if (token && ABBREVIATIONS.has(token[1].toLowerCase())) continue;
      }

      let next = index + 1;
      while (next < trimmed.length && /\s/.test(trimmed[next])) next += 1;
      if (next >= trimmed.length) break;
      if (next === index + 1) continue;

      const nextCharacter = trimmed[next];
      if (!/[A-Z0-9([“‘"'$\\]/.test(nextCharacter)) continue;

      const segment = trimmed.slice(start, index + 1).trim();
      if (segment) segments.push(segment);
      start = next;
      index = next - 1;
    }

    const tail = trimmed.slice(start).trim();
    if (tail) segments.push(tail);
  });

  return segments.length ? segments : [String(value)];
}

function formatAnswer(value = "") {
  return splitAnswer(value)
    .map((segment) => `<div class="answer-line">${richText(segment)}</div>`)
    .join("");
}

/* --------------------------------------------------------------- figures */

function figureHtml(image) {
  if (!image || typeof image !== "object" || !image.src) return "";
  const alt = image.alt || image.caption || "Question diagram";
  const caption = image.caption ? `<figcaption>${esc(image.caption)}</figcaption>` : "";
  return `<figure class="q-figure">
    <button type="button" class="q-figure-btn" data-zoom="${esc(image.src)}" data-zoom-alt="${esc(alt)}" data-zoom-caption="${esc(image.caption || "")}" aria-label="Enlarge diagram">
      <img src="${esc(image.src)}" alt="${esc(alt)}" loading="lazy" decoding="async">
    </button>
    ${caption}
  </figure>`;
}

function imageGallery(images, placement) {
  if (!Array.isArray(images)) return "";
  const selected = images.filter((image) => (image?.placement || "question") === placement);
  return selected.length
    ? `<div class="figure-grid">${selected.map(figureHtml).join("")}</div>`
    : "";
}

function questionImages(question) {
  return imageGallery(question.images, "question");
}

function solutionImages(question) {
  return imageGallery(question.images, "solution");
}

function openLightbox(src, alt, caption) {
  const box = $("#lightbox");
  if (!box) return;
  $("#lightbox-img").src = src;
  $("#lightbox-img").alt = alt || "Diagram";
  $("#lightbox-caption").textContent = caption || "";
  box.hidden = false;
  box.classList.add("is-open");
  document.body.classList.add("is-lightbox-open");
}

function closeLightbox() {
  const box = $("#lightbox");
  if (!box) return;
  box.classList.remove("is-open");
  box.hidden = true;
  document.body.classList.remove("is-lightbox-open");
}

function bindLightbox() {
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-zoom]");
    if (trigger) {
      openLightbox(trigger.dataset.zoom, trigger.dataset.zoomAlt, trigger.dataset.zoomCaption);
      return;
    }
    if (event.target.closest("#lightbox") || event.target.closest(".lightbox-close")) closeLightbox();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeLightbox();
  });
}

function chapterName(subjectId, chapterId) {
  const subject = state.bank.subjects.find((item) => item.id === subjectId);
  return subject?.chapters.find((item) => item.id === chapterId)?.name || chapterId;
}

function paperMap() {
  return Object.fromEntries(state.bank.papers.map((paper) => [paper.id, paper]));
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("is-on");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("is-on"), 2200);
}

function saveBookmarks() {
  localStorage.setItem("paperpal.bookmarks", JSON.stringify([...state.bookmarks]));
}

/* ---------------------------------------------------------------- boot */

// A per-load cache key stops browsers and GitHub Pages from serving a stale
// index/chunk after `paperpal build` regenerates the data.
const DATA_CACHE_BUST = Date.now();

let archivePromise = null;
let bankPromise = null;

function updateFooter() {
  const meta = state.archive?.meta || state.bank?.meta;
  if (!meta) return;
  const questionText = state.bank ? `${state.bank.meta.questionCount} questions · ` : "";
  $("#footer-meta").textContent =
    `${questionText}${meta.paperCount} local papers · ${meta.subjectCount} subjects`;
}

async function loadArchive() {
  if (state.archive) return state.archive;
  if (!archivePromise) {
    archivePromise = (async () => {
      const response = await fetch(`../data/archive.json?v=${DATA_CACHE_BUST}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.archive = await response.json();
      updateFooter();
      buildPaperFilters();
      if (currentView() === "papers") renderPapers();
      return state.archive;
    })();
  }
  try {
    return await archivePromise;
  } catch (error) {
    archivePromise = null;
    $("#paper-grid").innerHTML = `<div class="empty"><strong>Could not load the paper archive</strong>
      Run <code>uv run paperpal build</code>, then start the app with <code>uv run paperpal serve</code>.
      <br><br>${esc(error.message)}</div>`;
    throw error;
  }
}

function initializeBank() {
  state.subjectId = state.subjectId || state.bank.subjects[0]?.id || null;
  updateFooter();
  buildSubjectList();
  buildYearFilter();
  buildPracticeSelectors();
}

async function loadSubjectQuestions(subjectId) {
  if (!state.bank || !subjectId) return [];
  if (state.questionCache.has(subjectId)) {
    const questions = state.questionCache.get(subjectId);
    if (state.subjectId === subjectId) {
      state.bank.questions = questions;
      state.bank.papers = state.paperCache.get(subjectId) || [];
      state.loadedSubjectId = subjectId;
    }
    return questions;
  }

  const subject = state.bank.subjects.find((item) => item.id === subjectId);
  if (!subject?.questionFile) {
    const questions = state.bank.questions.filter((question) => question.subject === subjectId);
    state.questionCache.set(subjectId, questions);
    return questions;
  }

  if (!state.questionPromises.has(subjectId)) {
    state.questionPromises.set(subjectId, (async () => {
      const response = await fetch(`../data/${subject.questionFile}?v=${DATA_CACHE_BUST}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const questions = Array.isArray(payload) ? payload : payload.questions;
      if (!Array.isArray(questions)) throw new Error("Invalid subject question file");
      state.questionCache.set(subjectId, questions);
      state.paperCache.set(subjectId, Array.isArray(payload) ? [] : (payload.papers || []));
      return questions;
    })());
  }

  try {
    const questions = await state.questionPromises.get(subjectId);
    if (state.subjectId === subjectId) {
      state.bank.questions = questions;
      state.bank.papers = state.paperCache.get(subjectId) || [];
      state.loadedSubjectId = subjectId;
    }
    return questions;
  } catch (error) {
    state.questionPromises.delete(subjectId);
    throw error;
  }
}

async function loadBank() {
  if (state.bank) return state.bank;
  if (!bankPromise) {
    bankPromise = (async () => {
      const response = await fetch(`../data/bank-index.json?v=${DATA_CACHE_BUST}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.bank = { ...(await response.json()), papers: [], questions: [] };
      initializeBank();
      await loadSubjectQuestions(state.subjectId);
      updatePracticeHint();
      renderBank();
      return state.bank;
    })();
  }
  try {
    return await bankPromise;
  } catch (error) {
    bankPromise = null;
    $("#question-list").innerHTML = `<div class="empty"><strong>Could not load the question bank</strong>
      Run <code>uv run paperpal build</code>, then start the app with <code>uv run paperpal serve</code>.
      <br><br>${esc(error.message)}</div>`;
    throw error;
  }
}

async function init() {
  applyTheme(localStorage.getItem("paperpal.theme") || "dark");
  bindChrome();
  const view = currentView();
  switchView(view, false);
  if (view === "papers") {
    try { await loadArchive(); } catch { return; }
  } else if (view === "bank" || view === "practice") {
    try { await loadBank(); } catch { return; }
  }
}

function bindChrome() {
  const menuToggle = $("#menu-toggle");
  menuToggle.addEventListener("click", () => {
    const open = document.querySelector(".topbar").classList.toggle("nav-open");
    menuToggle.setAttribute("aria-expanded", String(open));
    menuToggle.setAttribute("aria-label", open ? "Close navigation menu" : "Open navigation menu");
    menuToggle.textContent = open ? "×" : "☰";
  });
  $$(".tab").forEach((tab) => tab.addEventListener("click", (event) => {
    event.preventDefault();
    switchView(tab.dataset.view);
    closeNavigationMenu();
  }));
  $$('[data-view]').forEach((control) => {
    if (control.classList.contains('tab')) return;
    control.addEventListener('click', (event) => {
      event.preventDefault();
      switchView(control.dataset.view);
    });
  });
  $$("[data-home-view]").forEach((control) => {
    control.addEventListener("click", () => switchView(control.dataset.homeView));
  });

  $("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem("paperpal.theme", next);
  });

  $("#search").addEventListener("input", (event) => {
    state.search = event.target.value.trim().toLowerCase();
    renderBank();
  });
  $("#subject-search").addEventListener("input", (event) => {
    state.subjectSearch = event.target.value.trim().toLowerCase();
    buildSubjectList();
  });
  $("#filter-type").addEventListener("change", (event) => { state.type = event.target.value; renderBank(); });
  $("#filter-year").addEventListener("change", (event) => { state.year = event.target.value; renderBank(); });

  $("#bookmark-filter").addEventListener("click", (event) => {
    state.bookmarkedOnly = !state.bookmarkedOnly;
    event.currentTarget.setAttribute("aria-pressed", String(state.bookmarkedOnly));
    renderBank();
  });

  $("#shuffle-btn").addEventListener("click", () => {
    state.shuffleOrder = state.shuffleOrder
      ? null
      : Object.fromEntries(state.bank.questions.map((question, index) => [question.id, Math.random() + index * 1e-9]));
    toast(state.shuffleOrder ? "Questions shuffled" : "Original order restored");
    renderBank();
  });

  $("#reset-filters").addEventListener("click", resetFilters);

  $("#question-list").addEventListener("click", handleBankClick);
  $("#paper-grid").addEventListener("click", handlePaperClick);
  $("#paper-subject").addEventListener("change", (event) => {
    state.paperSubject = event.target.value;
    state.paperPage = 1;
    renderPapers();
  });
  $("#paper-subject-search").addEventListener("input", (event) => {
    state.paperSubjectSearch = event.target.value.trim().toLowerCase();
    state.paperPage = 1;
    buildPaperFilters();
    renderPapers();
  });
  $("#paper-search").addEventListener("input", (event) => {
    state.paperSearch = event.target.value.trim().toLowerCase();
    state.paperPage = 1;
    renderPapers();
  });
  $("#paper-pagination").addEventListener("click", (event) => {
    const button = event.target.closest("[data-paper-page]");
    if (!button || button.disabled) return;
    state.paperPage = Number(button.dataset.paperPage);
    renderPapers();
    $("#view-papers").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("#practice-subject").addEventListener("change", async (event) => {
    state.practiceSubjectId = event.target.value;
    $("#practice-hint").textContent = "Loading questions…";
    try {
      await loadSubjectQuestions(state.practiceSubjectId);
      buildPracticeChapters();
    } catch (error) {
      $("#practice-hint").textContent = `Could not load questions (${error.message})`;
    }
  });
  ["#practice-chapter", "#practice-count"].forEach((selector) => {
    $(selector).addEventListener("change", updatePracticeHint);
  });
  $("#start-practice").addEventListener("click", startPractice);
  $("#quit-practice").addEventListener("click", endPractice);
  $("#quiz-reveal").addEventListener("click", () => {
    if (!state.practice) return;
    const id = currentPracticeQuestion().id;
    if (state.practice.revealed.has(id)) state.practice.revealed.delete(id);
    else state.practice.revealed.add(id);
    renderPractice();
  });
  $("#quiz-next").addEventListener("click", () => {
    if (!state.practice) return;
    state.practice.index += 1;
    if (state.practice.index >= state.practice.questions.length) state.practice.finished = true;
    renderPractice();
  });
  $("#quiz-body").addEventListener("click", handlePracticeClick);
  $("#practice-summary").addEventListener("click", (event) => {
    if (event.target.closest("[data-restart]")) {
      startPractice(state.practice?.questions || []);
    } else if (event.target.closest("[data-new-setup]")) {
      endPractice();
    }
  });

  bindLightbox();
  window.addEventListener("popstate", () => switchView(currentView(), false));
  window.addEventListener("hashchange", () => switchView(currentView(), false));
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeNavigationMenu();
  });
}

function closeNavigationMenu() {
  const topbar = $(".topbar");
  const menuToggle = $("#menu-toggle");
  if (!topbar || !menuToggle) return;
  topbar.classList.remove("nav-open");
  menuToggle.setAttribute("aria-expanded", "false");
  menuToggle.setAttribute("aria-label", "Open navigation menu");
  menuToggle.textContent = "☰";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
}

function currentView() {
  // Hash routing works on GitHub Pages project sites, where server-side
  // rewrites for /bank, /practice and /papers are not available.
  const hash = window.location.hash.replace(/^#\/?/, "").replace(/\/+$/, "");
  if (["home", "bank", "practice", "papers"].includes(hash)) return hash;
  const path = window.location.pathname.replace(/\/+$/, "");
  const route = path.split("/").pop();
  if (["bank", "practice", "papers"].includes(route)) return route;
  const view = new URLSearchParams(window.location.search).get("view");
  return ["home", "bank", "practice", "papers"].includes(view) ? view : "home";
}

function switchView(view, navigate = true) {
  if (!["home", "bank", "practice", "papers"].includes(view)) view = "home";
  if (navigate) {
    window.history.pushState({ view }, "", `#${view}`);
  }
  $$(".view").forEach((section) => section.classList.toggle("is-active", section.id === `view-${view}`));
  $$(".tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === view));
  if (view === "papers") {
    if (state.archive) renderPapers();
    else loadArchive().catch(() => {});
  }
  if (view === "bank" || view === "practice") {
    if (state.bank) {
      const subjectId = view === "practice"
        ? $("#practice-subject").value || state.practiceSubjectId
        : state.subjectId;
      loadSubjectQuestions(subjectId).then(() => {
        if (view === "practice") updatePracticeHint();
        else if (currentView() === "bank" && state.subjectId === subjectId) renderBank();
      }).catch((error) => {
        const target = view === "practice" ? $("#practice-hint") : $("#question-list");
        if (target) target.textContent = `Could not load questions (${error.message})`;
      });
    } else {
      loadBank().catch(() => {});
    }
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------------------------------------------------------------- bank */

function buildSubjectList() {
  const subjects = state.bank.subjects.filter((subject) => {
    if (!subject.paperCount || !subject.questionCount) return false;
    if (!state.subjectSearch) return true;
    const haystack = [subject.name, subject.shortName, subject.id, semesterLabel(subject)].filter(Boolean).join(" ").toLowerCase();
    return haystack.includes(state.subjectSearch);
  });
  $("#subject-list").innerHTML = subjects
    .map((subject) => `
      <button class="subject-btn ${subject.id === state.subjectId ? "is-active" : ""}" data-subject="${subject.id}">
        <span class="subject-dot"></span>
        <span>${esc(subject.shortName)}</span>
        ${semesterLabel(subject) ? `<span class="subject-semester">${esc(semesterLabel(subject))}</span>` : ""}
        <span class="subject-count">${subject.questionCount}</span>
      </button>`)
    .join("") || '<div class="empty subject-empty">No matching subjects.</div>';

  $$("#subject-list .subject-btn").forEach((button) => button.addEventListener("click", async () => {
    state.subjectId = button.dataset.subject;
    state.chapterId = "all";
    state.paperId = null;
    state.search = "";
    $("#search").value = "";
    buildSubjectList();
    buildChapterList();
    $("#question-list").innerHTML = '<div class="empty"><strong>Loading questions…</strong></div>';
    try {
      await loadSubjectQuestions(state.subjectId);
      renderBank();
    } catch (error) {
      $("#question-list").innerHTML = `<div class="empty"><strong>Could not load this subject</strong><br>${esc(error.message)}</div>`;
    }
  }));
  buildChapterList();
}

function buildChapterList() {
  const subject = state.bank.subjects.find((item) => item.id === state.subjectId);
  if (!subject) return;
  $("#chapter-total").textContent = subject.questionCount;
  const items = [{ id: "all", name: "All chapters", questionCount: subject.questionCount }, ...subject.chapters];
  $("#chapter-list").innerHTML = items
    .map((chapter) => `
      <button class="chapter-btn ${state.chapterId === chapter.id ? "is-active" : ""}" data-chapter="${chapter.id}">
        <span>${esc(chapter.name)}</span><span class="chapter-count">${chapter.questionCount}</span>
      </button>`)
    .join("");
  $$("#chapter-list .chapter-btn").forEach((button) => button.addEventListener("click", () => {
    state.chapterId = button.dataset.chapter;
    buildChapterList();
    renderBank();
  }));
}

function buildYearFilter() {
  const years = state.bank.years || [];
  $("#filter-year").insertAdjacentHTML("beforeend", years.map((year) => `<option value="${year}">${year}</option>`).join(""));
}

function resetFilters() {
  state.chapterId = "all";
  state.type = "all";
  state.year = "all";
  state.search = "";
  state.subjectSearch = "";
  state.bookmarkedOnly = false;
  state.paperId = null;
  state.shuffleOrder = null;
  $("#search").value = "";
  $("#subject-search").value = "";
  $("#filter-type").value = "all";
  $("#filter-year").value = "all";
  $("#bookmark-filter").setAttribute("aria-pressed", "false");
  buildChapterList();
  renderBank();
}

function filteredQuestions() {
  const papers = paperMap();
  return state.bank.questions.filter((question) => {
    if (question.subject !== state.subjectId) return false;
    if (state.chapterId !== "all" && question.chapter !== state.chapterId) return false;
    if (state.type !== "all" && question.type !== state.type) return false;
    if (state.year !== "all" && !question.papers.some((ref) => papers[ref.id]?.year === Number(state.year))) return false;
    if (state.paperId && !question.papers.some((ref) => ref.id === state.paperId)) return false;
    if (state.bookmarkedOnly && !state.bookmarks.has(question.id)) return false;
    if (state.search) {
      const haystack = [
        question.question, question.topic, question.solution, question.note,
        chapterName(question.subject, question.chapter),
        ...(question.options || []),
        ...question.papers.map((ref) => `${papers[ref.id]?.code || ""} ${papers[ref.id]?.exam || ""} ${papers[ref.id]?.year || ""}`),
      ].join(" ").toLowerCase();
      if (!haystack.includes(state.search)) return false;
    }
    return true;
  }).sort((a, b) => {
    if (!state.shuffleOrder) return 0;
    return state.shuffleOrder[a.id] - state.shuffleOrder[b.id];
  });
}

function renderBank() {
  const subject = state.bank.subjects.find((item) => item.id === state.subjectId);
  const chapter = subject?.chapters.find((item) => item.id === state.chapterId);
  const questions = filteredQuestions();

  $("#bank-eyebrow").textContent = state.paperId ? "Paper practice" : chapter ? "Chapter" : "Subject";
  $("#bank-title").textContent = chapter?.name || subject?.name || "Question bank";
  const description = chapter?.description || subject?.description || "";
  $("#bank-subtitle").textContent = description;

  const pills = [];
  if (state.paperId) {
    const paper = paperMap()[state.paperId];
    pills.push(`<span class="filter-pill">${esc(paper.title)} · ${paper.year}
      <button data-clear-paper aria-label="Clear paper filter">×</button></span>`);
  }
  if (state.bookmarkedOnly) pills.push(`<span class="filter-pill">Bookmarked <button data-clear-bookmarks aria-label="Clear bookmark filter">×</button></span>`);
  if (state.search) pills.push(`<span class="filter-pill">Search: ${esc(state.search)} <button data-clear-search aria-label="Clear search">×</button></span>`);
  $("#active-filters").innerHTML = pills.join("");
  $("#active-filters").onclick = (event) => {
    if (event.target.closest("[data-clear-paper]")) { state.paperId = null; renderBank(); }
    if (event.target.closest("[data-clear-bookmarks]")) { state.bookmarkedOnly = false; $("#bookmark-filter").setAttribute("aria-pressed", "false"); renderBank(); }
    if (event.target.closest("[data-clear-search]")) { state.search = ""; $("#search").value = ""; renderBank(); }
  };

  const marks = questions.reduce((total, question) => total + question.marks, 0);
  const mcq = questions.filter((question) => question.type === "MCQ").length;
  const auto = questions.filter((question) => question.autoImported).length;
  $("#result-line").textContent = questions.length + " question" + (questions.length === 1 ? "" : "s")
    + " · " + mcq + " MCQ · " + marks + " marks total"
    + (auto ? " · " + auto + " auto-imported" : "");

  $("#question-list").innerHTML = questions.length
    ? questions.map(questionCard).join("")
    : `<div class="empty"><strong>No questions here</strong>Try clearing a filter or choosing another chapter.</div>`;
}

function questionCard(question) {
  const answered = state.answered.get(question.id);
  const revealed = state.revealed.has(question.id) || answered !== undefined;
  const starred = state.bookmarks.has(question.id);
  const repeated = question.papers.length > 1;

  const options = (question.options || []).map((option, index) => {
    let className = "";
    if (revealed && index === question.answer) className = "is-correct";
    if (answered === index && index !== question.answer) className = "is-wrong";
    return `<button class="option ${className}" data-option="${index}" data-qid="${question.id}" ${answered !== undefined ? "disabled" : ""}>
      <span class="letter">${String.fromCharCode(65 + index)}</span><span>${richText(option)}</span>
    </button>`;
  }).join("");

  return `<article class="q-card" data-card="${question.id}">
    <div class="q-top">
      <span class="badge chapter">${esc(chapterName(question.subject, question.chapter))}</span>
      <span class="badge">${question.type === "MCQ" ? "MCQ" : "Descriptive"}</span>
      ${question.autoImported ? '<span class="badge auto-import">Auto-imported · no official solution</span>' : ""}
      ${repeated ? `<span class="badge repeat">${question.papers.length}× repeated</span>` : ""}
      <span class="q-marks">${question.marks} mark${question.marks === 1 ? "" : "s"}</span>
      <button class="star-btn ${starred ? "is-on" : ""}" data-star="${question.id}" aria-label="${starred ? "Remove bookmark" : "Bookmark question"}">${starred ? "★" : "☆"}</button>
    </div>
    <p class="q-text">${richText(question.question)}</p>
    ${questionImages(question)}
    ${options ? `<div class="options">${options}</div>` : ""}
    ${revealed ? solutionBlock(question) : ""}
    ${question.note ? `<p class="note">${richText(question.note)}</p>` : ""}
    <div class="q-foot">
      <span class="q-source">${sourceLine(question)}</span>
      <button class="link-btn" data-reveal="${question.id}">${revealed ? "Hide solution" : question.type === "MCQ" ? "Reveal answer" : "Show solution"}</button>
    </div>
  </article>`;
}

function solutionBlock(question) {
  const label = question.autoImported
    ? "No official solution"
    : question.type === "MCQ" ? "Answer & explanation" : "Model answer";
  return `<div class="solution"><span class="label">${label}</span><div class="answer-body">${formatAnswer(question.solution)}</div>${solutionImages(question)}</div>`;
}

function sourceLine(question) {
  const papers = paperMap();
  return question.papers.map((ref) => {
    const paper = papers[ref.id];
    if (!paper) return esc(ref.id);
    return `${esc(paper.code)} · ${esc(paper.exam)} ${paper.year} · ${esc(ref.qno)}`;
  }).join(" &nbsp;|&nbsp; ");
}

function handleBankClick(event) {
  const star = event.target.closest("[data-star]");
  if (star) {
    const id = star.dataset.star;
    state.bookmarks.has(id) ? state.bookmarks.delete(id) : state.bookmarks.add(id);
    saveBookmarks();
    refreshCard(id);
    return;
  }

  const reveal = event.target.closest("[data-reveal]");
  if (reveal) {
    const id = reveal.dataset.reveal;
    state.revealed.has(id) ? state.revealed.delete(id) : state.revealed.add(id);
    refreshCard(id);
    return;
  }

  const option = event.target.closest("[data-option]");
  if (option) {
    const id = option.dataset.qid;
    if (state.answered.has(id)) return;
    state.answered.set(id, Number(option.dataset.option));
    refreshCard(id);
  }
}

function refreshCard(id) {
  const question = state.bank.questions.find((item) => item.id === id);
  const card = document.querySelector(`[data-card="${CSS.escape(id)}"]`);
  if (question && card) card.outerHTML = questionCard(question);
}

/* ---------------------------------------------------------------- practice */

function buildPracticeSelectors() {
  $("#practice-subject").innerHTML = state.bank.subjects
    .map((subject) => `<option value="${subject.id}">${esc(subject.name)}</option>`)
    .join("");
  state.practiceSubjectId = state.bank.subjects[0]?.id;
  buildPracticeChapters();
}

function buildPracticeChapters() {
  const subject = state.bank.subjects.find((item) => item.id === state.practiceSubjectId);
  $("#practice-chapter").innerHTML = `<option value="all">All chapters (${subject.questionCount})</option>` +
    subject.chapters.map((chapter) => `<option value="${chapter.id}">${esc(chapter.name)} (${chapter.questionCount})</option>`).join("");
  updatePracticeHint();
}

function practiceMatches() {
  const subjectId = $("#practice-subject").value;
  const chapterId = $("#practice-chapter").value;
  const questions = state.questionCache.get(subjectId) || [];
  return questions.filter((question) =>
    question.subject === subjectId &&
    (chapterId === "all" || question.chapter === chapterId));
}

function updatePracticeHint() {
  if (!state.bank) return;
  const matches = practiceMatches();
  $("#practice-hint").textContent = `${matches.length} question${matches.length === 1 ? "" : "s"} available`;
}

function startPractice(presetQuestions) {
  const questionSet = Array.isArray(presetQuestions) && presetQuestions.length
    ? [...presetQuestions].sort(() => Math.random() - 0.5)
    : [...practiceMatches()].sort(() => Math.random() - 0.5);
  const requested = Number($("#practice-count").value) || questionSet.length;
  const questions = questionSet.slice(0, requested);

  if (!questions.length) {
    toast("No questions match that selection");
    return;
  }

  state.practice = { questions, index: 0, answers: new Map(), revealed: new Set(), finished: false };
  $("#practice-setup").classList.add("is-hidden");
  $("#practice-summary").classList.add("is-hidden");
  $("#practice-quiz").classList.remove("is-hidden");
  renderPractice();
}

function currentPracticeQuestion() {
  return state.practice.questions[state.practice.index];
}

function practiceScore() {
  if (!state.practice) return { correct: 0, attempted: 0, totalMcq: 0 };
  const totalMcq = state.practice.questions.filter((question) => question.type === "MCQ").length;
  let correct = 0;
  let attempted = 0;
  for (const question of state.practice.questions) {
    if (question.type !== "MCQ" || !state.practice.answers.has(question.id)) continue;
    attempted += 1;
    if (state.practice.answers.get(question.id) === question.answer) correct += 1;
  }
  return { correct, attempted, totalMcq };
}

function renderPractice() {
  const practice = state.practice;
  if (!practice) return;

  if (practice.finished) {
    renderPracticeSummary();
    return;
  }

  const question = currentPracticeQuestion();
  const revealed = practice.revealed.has(question.id) || practice.answers.has(question.id);
  const selected = practice.answers.get(question.id);
  const { correct, totalMcq } = practiceScore();

  $("#quiz-progress-text").textContent = `Question ${practice.index + 1} of ${practice.questions.length}`;
  $("#quiz-score").textContent = totalMcq ? `${correct} / ${totalMcq} MCQ correct` : "Descriptive set";
  $("#quiz-progress").style.width = `${((practice.index + 1) / practice.questions.length) * 100}%`;
  $("#quiz-reveal").textContent = revealed ? "Hide solution" : "Reveal solution";
  $("#quiz-reveal").classList.toggle("is-hidden", question.type === "MCQ" && revealed);
  $("#quiz-next").textContent = practice.index === practice.questions.length - 1 ? "Finish →" : "Next question →";

  const options = (question.options || []).map((option, index) => {
    let className = "";
    if (revealed && index === question.answer) className = "is-correct";
    if (selected === index && index !== question.answer) className = "is-wrong";
    return `<button class="option ${className}" data-practice-option="${index}" ${selected !== undefined ? "disabled" : ""}>
      <span class="letter">${String.fromCharCode(65 + index)}</span><span>${richText(option)}</span>
    </button>`;
  }).join("");

  $("#quiz-body").innerHTML = `<article class="q-card" data-card="${question.id}">
    <div class="q-top">
      <span class="badge chapter">${esc(chapterName(question.subject, question.chapter))}</span>
      <span class="badge">${question.type === "MCQ" ? "MCQ" : "Descriptive"}</span>
      <span class="q-marks">${question.marks} mark${question.marks === 1 ? "" : "s"}</span>
    </div>
    <p class="q-text">${richText(question.question)}</p>
    ${questionImages(question)}
    ${options ? `<div class="options">${options}</div>` : ""}
    ${revealed ? solutionBlock(question) : ""}
    ${question.note ? `<p class="note">${richText(question.note)}</p>` : ""}
    <div class="q-foot"><span class="q-source">${sourceLine(question)}</span></div>
  </article>`;
}

function handlePracticeClick(event) {
  const option = event.target.closest("[data-practice-option]");
  if (!option || !state.practice) return;
  const question = currentPracticeQuestion();
  if (state.practice.answers.has(question.id)) return;
  state.practice.answers.set(question.id, Number(option.dataset.practiceOption));
  renderPractice();
}

function renderPracticeSummary() {
  const practice = state.practice;
  const { correct, attempted, totalMcq } = practiceScore();
  const descriptive = practice.questions.length - totalMcq;
  const percent = totalMcq ? Math.round((correct / totalMcq) * 100) : null;
  const review = practice.questions.map((question) => {
    let status = "–";
    let className = "";
    if (question.type === "MCQ" && practice.answers.has(question.id)) {
      const right = practice.answers.get(question.id) === question.answer;
      status = right ? "✓" : "✗";
      className = right ? "result-correct" : "result-wrong";
    }
    return `<details class="solution">
      <summary><span class="badge ${className}">${status}</span> ${richText(question.question)}</summary>
      ${questionImages(question)}
      <div class="answer-body" style="margin-top:8px">${formatAnswer(question.solution)}</div>
      ${solutionImages(question)}
    </details>`;
  }).join("");

  $("#practice-quiz").classList.add("is-hidden");
  $("#practice-summary").classList.remove("is-hidden");
  $("#practice-summary").innerHTML = `<div class="quiz-result">
    <p class="eyebrow">Session complete</p>
    <div class="score">${percent === null ? "—" : percent + "<small>%</small>"}</div>
    <h3>${totalMcq ? `${correct} of ${totalMcq} MCQs correct` : "Descriptive practice complete"}</h3>
    <p>${practice.questions.length} questions · ${attempted} MCQ attempted · ${descriptive} descriptive</p>
    <div class="setup-actions">
      <button class="primary-btn" data-restart>Try this set again</button>
      <button class="ghost-btn" data-new-setup>New practice set</button>
    </div>
  </div>
  <h3 style="margin:26px 0 10px;font-family:var(--serif);font-size:18px">Review</h3>
  <div style="display:grid;gap:8px">${review}</div>`;
}

function endPractice() {
  state.practice = null;
  $("#practice-quiz").classList.add("is-hidden");
  $("#practice-summary").classList.add("is-hidden");
  $("#practice-setup").classList.remove("is-hidden");
  updatePracticeHint();
}

/* ---------------------------------------------------------------- papers */

function buildPaperFilters() {
  if (!state.archive) return;
  const subjects = state.archive.subjects.filter((subject) =>
    !state.paperSubjectSearch || subject.name.toLowerCase().includes(state.paperSubjectSearch)
  );
  const selected = state.archive.subjects.find((subject) => subject.id === state.paperSubject);
  if (state.paperSubject !== "all" && (!selected || !subjects.includes(selected))) state.paperSubject = "all";
  $("#paper-subject").innerHTML = [
    `<option value="all">All subjects (${state.archive.meta.subjectCount})</option>`,
    ...subjects.map((subject) => `<option value="${esc(subject.id)}">${esc(subject.name)}${semesterLabel(subject) ? ` · ${esc(semesterLabel(subject))}` : ""} (${subject.paperCount})</option>`),
  ].join("");
  $("#paper-subject").value = state.paperSubject;
}

function renderPapers() {
  if (!state.archive) return;
  const query = state.paperSearch;
  const papers = state.archive.papers
    .filter((paper) => state.paperSubject === "all" || paper.subject === state.paperSubject)
    .filter((paper) => {
      if (!query) return true;
      const haystack = [paper.title, paper.code, paper.subjectName, semesterLabel(paper), paper.year, paper.month, paper.exam]
        .join(" ").toLowerCase();
      return haystack.includes(query);
    })
    .sort((a, b) => (b.year || 0) - (a.year || 0) || a.title.localeCompare(b.title));

  const pageSize = 48;
  const pageCount = Math.max(1, Math.ceil(papers.length / pageSize));
  state.paperPage = Math.min(Math.max(state.paperPage, 1), pageCount);
  const pageStart = (state.paperPage - 1) * pageSize;
  const visiblePapers = papers.slice(pageStart, pageStart + pageSize);
  $("#paper-total").textContent = `${papers.length} papers · ${state.archive.meta.subjectCount} subjects`;

  $("#paper-grid").innerHTML = visiblePapers.map((paper) => `
    <article class="paper-card">
      <div class="paper-top">
        <div>
          <p class="paper-code">${esc(paper.subjectName)}${paper.code ? ` · ${esc(paper.code)}` : ""}</p>
          <h3>${esc(paper.title)}</h3>
        </div>
        <span class="paper-year">${paper.year || "—"}</span>
      </div>
      <div class="paper-meta">
        ${paper.exam ? `<span>${esc(paper.exam)}</span>` : ""}
        ${paper.semester ? `<span>Sem ${paper.semester}</span>` : ""}
        ${paper.month ? `<span>${esc(paper.month)}</span>` : ""}
        ${paper.maxMarks ? `<span>${paper.maxMarks} marks</span>` : ""}
        ${paper.duration ? `<span>${esc(paper.duration)}</span>` : ""}
        ${paper.questionCount ? `<span>${paper.questionCount} in bank</span>` : ""}
      </div>
      <div class="paper-chapters">
        ${paper.chapterIds.map((chapterId) => `<span class="chapter-tag">${esc(chapterName(paper.subject, chapterId))}</span>`).join("")}
      </div>
      ${paper.notes ? `<p class="q-source">${esc(paper.notes)}</p>` : ""}
      <div class="paper-actions">
        <a class="primary-link" href="${encodeURI("../" + paper.file)}" target="_blank" rel="noopener">Open paper PDF</a>
        ${paper.solutionFile && paper.solutionFile !== paper.file ? `<a href="${encodeURI("../" + paper.solutionFile)}" target="_blank" rel="noopener">Solutions</a>` : ""}
        ${paper.solutionFile === paper.file ? `<span class="chapter-tag">Solutions included</span>` : ""}
        ${paper.answerScript ? `<a href="${encodeURI("../" + paper.answerScript)}" target="_blank" rel="noopener">Answer script</a>` : ""}
        ${paper.questionCount ? `<button data-practice-paper="${paper.id}">Practice this paper</button>` : ""}
      </div>
    </article>`).join("") || `<div class="empty"><strong>No papers</strong>Try another subject.</div>`;

  $("#paper-pagination").innerHTML = pageCount > 1 ? `
    <button class="ghost-btn" data-paper-page="${Math.max(1, state.paperPage - 1)}" ${state.paperPage === 1 ? "disabled" : ""}>← Previous</button>
    <span>Page ${state.paperPage} of ${pageCount}</span>
    <button class="ghost-btn" data-paper-page="${Math.min(pageCount, state.paperPage + 1)}" ${state.paperPage === pageCount ? "disabled" : ""}>Next →</button>` : "";
}

function handlePaperClick(event) {
  const button = event.target.closest("[data-practice-paper]");
  if (!button) return;
  const paper = state.archive.papers.find((item) => item.id === button.dataset.practicePaper);
  if (!paper || !paper.questionCount) return;
  state.subjectId = paper.subject;
  state.chapterId = "all";
  state.paperId = paper.id;
  state.type = "all";
  state.year = "all";
  state.search = "";
  state.bookmarkedOnly = false;
  $("#search").value = "";
  $("#filter-type").value = "all";
  $("#filter-year").value = "all";
  $("#bookmark-filter").setAttribute("aria-pressed", "false");
  if (state.bank) buildSubjectList();
  switchView("bank");
  toast(`Practising ${paper.code} · ${paper.year}`);
}

init();
