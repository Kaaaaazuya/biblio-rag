// PDF を presigned URL で S3(MinIO) に直接アップロードし、取り込みパイプラインを自動起動する。
const form = document.getElementById("form");
const fileInput = document.getElementById("file");
const titleInput = document.getElementById("title");
const authorInput = document.getElementById("author");
const submitBtn = document.getElementById("submit");
const progress = document.getElementById("progress");
const statusEl = document.getElementById("status");

const STEP_LABELS = {
  pending: "待機中…",
  extracting: "PDF を抽出中…",
  chunking: "チャンク分割中…",
  embedding: "埋め込み・格納中…（数分かかります）",
  done: "完了",
  failed: "失敗",
  unknown: "不明",
};

function setStatus(msg, kind = "") {
  statusEl.textContent = msg;
  statusEl.className = "status" + (kind ? " " + kind : "");
}

// presigned URL に XHR で PUT（進捗表示のため fetch ではなく XHR）
function putToS3(url, file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", "application/pdf");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) progress.value = Math.round((e.loaded / e.total) * 80);
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`アップロード失敗 (HTTP ${xhr.status})`));
    xhr.onerror = () => reject(new Error("ネットワークエラー（MinIO 起動と CORS を確認）"));
    xhr.send(file);
  });
}

async function postJSON(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${path} 失敗 (HTTP ${res.status})`);
  }
  return res.json();
}

async function pollIngestStatus(book_id) {
  while (true) {
    await new Promise((r) => setTimeout(r, 2000));
    const { status, error } = await fetch(`/api/ingest/${book_id}/status`).then((r) => r.json());
    const label = STEP_LABELS[status] || status;
    if (status === "done") {
      progress.value = 100;
      setStatus(`完了: book_id=${book_id}`, "ok");
      await loadBookList();
      return;
    }
    if (status === "failed") {
      setStatus(`失敗: ${error || label}`, "err");
      return;
    }
    setStatus(label);
  }
}

// ── 書籍一覧・削除（Issue #24） ────────────────────────────────────────────────

const bookListEl = document.getElementById("book-list");
const bookListEmptyEl = document.getElementById("book-list-empty");

async function loadBookList() {
  try {
    const res = await fetch("/api/books");
    if (!res.ok) return;
    const books = await res.json();
    renderBookList(books);
  } catch (_) { /* 一覧取得失敗時は表示を変更しない */ }
}

function renderBookList(books) {
  bookListEl.innerHTML = "";
  bookListEmptyEl.hidden = books.length > 0;

  for (const b of books) {
    const li = document.createElement("li");

    const title = document.createElement("span");
    title.className = "book-title";
    title.textContent = b.title || b.book_id;
    li.appendChild(title);

    if (b.author) {
      const author = document.createElement("span");
      author.className = "book-author";
      author.textContent = b.author;
      li.appendChild(author);
    }

    const delBtn = document.createElement("button");
    delBtn.className = "delete";
    delBtn.textContent = "削除";
    delBtn.addEventListener("click", () => deleteBook(b.book_id, b.title || b.book_id));
    li.appendChild(delBtn);

    bookListEl.appendChild(li);
  }
}

async function deleteBook(book_id, label) {
  if (!confirm(`「${label}」を削除しますか？この操作は取り消せません。`)) return;
  try {
    const res = await fetch(`/api/books/${encodeURIComponent(book_id)}`, { method: "DELETE" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert(body.detail || `削除に失敗しました (HTTP ${res.status})`);
      // 502: 検索データ（pgvector）側は削除済みのため一覧を更新する
      if (res.status === 502) await loadBookList();
      return;
    }
    await loadBookList();
  } catch (err) {
    alert(err.message);
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;

  submitBtn.disabled = true;
  progress.hidden = false;
  progress.value = 0;
  try {
    setStatus("署名を取得中…");
    const { url, key, book_id } = await postJSON("/api/presign", {
      filename: file.name,
      content_type: "application/pdf",
    });

    setStatus(`アップロード中… (${key})`);
    await putToS3(url, file);
    progress.value = 85;

    setStatus("メタデータを保存中…");
    await postJSON("/api/meta", {
      book_id,
      title: titleInput.value.trim(),
      author: authorInput.value.trim(),
    });
    progress.value = 90;

    setStatus("取り込みを開始中…");
    await postJSON("/api/ingest", { book_id });

    form.reset();
    await pollIngestStatus(book_id);
  } catch (err) {
    setStatus(err.message, "err");
  } finally {
    submitBtn.disabled = false;
  }
});

// ── 起動時に書籍一覧を取得 ────────────────────────────────────────────────────
loadBookList();
