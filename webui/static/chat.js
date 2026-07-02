/** RAG チャット UI — fetch + ReadableStream で POST-based SSE を処理 */

const messagesEl    = document.getElementById("messages");
const emptyEl       = document.getElementById("empty");
const queryEl       = document.getElementById("query");
const sendBtn       = document.getElementById("send-btn");
const abortBtn      = document.getElementById("abort-btn");
const clearBtn      = document.getElementById("clear-btn");
const personaSelect = document.getElementById("persona-select");
const langSelect    = document.getElementById("lang-select");

const modal      = document.getElementById("source-modal");
const modalTitle = document.getElementById("modal-title");
const modalSub   = document.getElementById("modal-sub");
const modalBody  = document.getElementById("modal-body");
const modalClose = document.getElementById("modal-close");

const STORAGE_KEY = "biblio-rag:chat-v1";

// 会話履歴（Ollama messages フォーマット）
const history = [];
// 表示用メッセージ（ページ復元用）
const displayed = []; // {role, text}

// AbortController for in-flight requests
let abortController = null;

// ── 永続化 ────────────────────────────────────────────────────────────────────

function saveStorage() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      history,
      displayed,
      lang: langSelect.value,
      persona: personaSelect.value,
    }));
  } catch (_) { /* quota over 等は無視 */ }
}

function loadStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const { history: h, displayed: d, lang, persona } = JSON.parse(raw);
    history.push(...(h ?? []));
    (d ?? []).forEach(({ role, text }) => addMessage(role, text));
    if (lang) langSelect.value = lang;
    if (persona) personaSelect.value = persona;
  } catch (_) { /* 破損データは無視 */ }
}

function clearStorage() {
  localStorage.removeItem(STORAGE_KEY);
  history.length = 0;
  displayed.length = 0;
  messagesEl.innerHTML = "";
  messagesEl.appendChild(emptyEl);
  emptyEl.style.display = "";
}

clearBtn.addEventListener("click", () => {
  if (confirm("会話履歴をクリアしますか？")) clearStorage();
});

// ── モーダル ──────────────────────────────────────────────────────────────────

function openModal(source) {
  modalTitle.textContent = source.title || "";
  modalSub.textContent = [
    source.author,
    source.chapter,
    source.section,
    source.page ? `p.${source.page}` : null,
  ].filter(Boolean).join(" · ");
  modalBody.textContent = source.text || "（原文なし）";
  modal.hidden = false;
}

function closeModal() { modal.hidden = true; }

modalClose.addEventListener("click", closeModal);
modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

// ── DOM helpers ──────────────────────────────────────────────────────────────

function addMessage(role, text = "") {
  emptyEl.style.display = "none";

  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (text) bubble.textContent = text;
  wrap.appendChild(bubble);

  messagesEl.appendChild(wrap);
  scrollBottom();
  return bubble;
}

function showSpinner(bubble) {
  const s = document.createElement("span");
  s.className = "spinner";
  bubble.appendChild(s);
  return s;
}

function addSources(sources) {
  const valid = sources.filter((s) => s.title);
  if (!valid.length) return;

  const wrap = messagesEl.lastElementChild;
  const row = document.createElement("div");
  row.className = "sources";

  valid.forEach((s) => {
    const label = [s.title, s.chapter, s.page ? `p.${s.page}` : null]
      .filter(Boolean).join(" · ");
    const chip = document.createElement("span");
    chip.className = "source-chip";
    chip.textContent = label;
    chip.addEventListener("click", () => openModal(s));
    row.appendChild(chip);
  });

  wrap.appendChild(row);
}

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setLoading(loading) {
  sendBtn.disabled = loading;
  queryEl.disabled = loading;
  clearBtn.disabled = loading;
  personaSelect.disabled = loading;
  langSelect.disabled = loading;
  sendBtn.classList.toggle("loading", loading);
  sendBtn.textContent = loading ? "" : "↑";
  // Show/hide abort button
  abortBtn.classList.toggle("visible", loading);
}

// ── Auto-resize textarea ──────────────────────────────────────────────────────

queryEl.addEventListener("input", () => {
  queryEl.style.height = "auto";
  queryEl.style.height = `${queryEl.scrollHeight}px`;
});

// ── Send（Enter = 改行 / Shift+Enter = 送信） ─────────────────────────────────

queryEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

sendBtn.addEventListener("click", sendMessage);
abortBtn.addEventListener("click", () => {
  if (abortController) {
    abortController.abort();
  }
});

async function sendMessage() {
  const query = queryEl.value.trim();
  if (!query || sendBtn.disabled) return;

  queryEl.value = "";
  queryEl.style.height = "auto";

  addMessage("user", query);
  const bubble = addMessage("assistant");
  const spinner = showSpinner(bubble);

  setLoading(true);

  let fullContent = "";
  let firstToken = true;
  let pendingSources = [];

  // Create new AbortController for this request
  abortController = new AbortController();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        history: history.slice(),
        persona: personaSelect.value,
        lang: langSelect.value,
      }),
      signal: abortController.signal,
    });

    if (!resp.ok) {
      const j = await resp.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let evt;
        try { evt = JSON.parse(line.slice(6)); } catch { continue; }

        if (evt.type === "sources") {
          pendingSources = evt.sources;
        } else if (evt.type === "token") {
          if (firstToken) { spinner.remove(); firstToken = false; }
          fullContent += evt.content;
          bubble.textContent = fullContent;
          scrollBottom();
        } else if (evt.type === "done") {
          break;
        } else if (evt.type === "error") {
          throw new Error(evt.message);
        }
      }
    }

    if (fullContent) {
      try {
        const html = marked.parse(fullContent);
        // XSS protection: Markdown レンダリング結果をサニタイズ
        if (typeof DOMPurify === "undefined") {
          console.error("DOMPurify not loaded; XSS protection unavailable");
          bubble.textContent = fullContent;
        } else {
          bubble.innerHTML = DOMPurify.sanitize(html);
          bubble.classList.add("rendered");
        }
      } catch (_) {
        bubble.textContent = fullContent;
      }
    }
    addSources(pendingSources);
    history.push({ role: "user", content: query });
    history.push({ role: "assistant", content: fullContent });
    displayed.push({ role: "user", text: query });
    displayed.push({ role: "assistant", text: fullContent });
    saveStorage();
  } catch (err) {
    spinner.remove();
    // Handle abort separately
    if (err.name === "AbortError") {
      bubble.dataset.error = "1";
      bubble.textContent = fullContent || "📍 キャンセルされました";
    } else {
      bubble.dataset.error = "1";
      bubble.textContent = fullContent || "⚠ エラーが発生しました";
    }
    scrollBottom();
  } finally {
    setLoading(false);
    abortController = null;
    queryEl.focus();
  }
}

// ── 起動時に履歴を復元 ────────────────────────────────────────────────────────
loadStorage();
