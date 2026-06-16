// PDF を presigned URL で S3(MinIO) に直接アップロードし、メタデータを保存する。
const form = document.getElementById("form");
const fileInput = document.getElementById("file");
const titleInput = document.getElementById("title");
const authorInput = document.getElementById("author");
const submitBtn = document.getElementById("submit");
const progress = document.getElementById("progress");
const statusEl = document.getElementById("status");

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
      if (e.lengthComputable) progress.value = Math.round((e.loaded / e.total) * 100);
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

    setStatus("メタデータを保存中…");
    await postJSON("/api/meta", {
      book_id,
      title: titleInput.value.trim(),
      author: authorInput.value.trim(),
    });

    setStatus(`完了: ${key}（book_id=${book_id}）。次に extract→chunk→embed を実行してください。`, "ok");
    form.reset();
  } catch (err) {
    setStatus(err.message, "err");
  } finally {
    submitBtn.disabled = false;
  }
});
