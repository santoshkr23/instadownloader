(() => {
  const PLATFORMS = [
    { key: "instagram", name: "Instagram", re: /(^|\.)(instagram\.com|instagr\.am)$/ },
    { key: "youtube", name: "YouTube", re: /(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$/ },
    { key: "pinterest", name: "Pinterest", re: /(^|\.)(pinterest\.[a-z.]+|pin\.it)$/ },
    { key: "twitter", name: "Twitter / X", re: /(^|\.)(twitter\.com|x\.com|t\.co)$/ },
    { key: "facebook", name: "Facebook", re: /(^|\.)(facebook\.com|fb\.watch|fb\.com)$/ },
  ];

  const $ = (id) => document.getElementById(id);
  const form = $("dl-form"), input = $("url"), go = $("go");
  const errBox = $("error"), loading = $("loading"), result = $("result"), detected = $("detected");
  if (!form) return;

  const detect = (url) => {
    try {
      const host = new URL(url.trim()).hostname.toLowerCase();
      return PLATFORMS.find((p) => p.re.test(host)) || null;
    } catch { return null; }
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDur = (s) => {
    if (!s) return "";
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(sec).padStart(2, "0");
  };

  const showError = (msg) => { errBox.textContent = msg; errBox.hidden = false; };
  const clearError = () => { errBox.hidden = true; errBox.textContent = ""; };

  input.addEventListener("input", () => {
    const p = detect(input.value);
    detected.hidden = !p;
    if (p) detected.textContent = `Detected: ${p.name}`;
  });

  $("paste")?.addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) { input.value = text.trim(); input.dispatchEvent(new Event("input")); }
    } catch { input.focus(); }
  });

  // allow ?url=... deep links
  const qs = new URLSearchParams(location.search).get("url");
  if (qs) { input.value = qs; input.dispatchEvent(new Event("input")); setTimeout(() => form.requestSubmit(), 0); }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearError();
    const url = input.value.trim();
    if (!detect(url)) {
      showError("Paste a link from Instagram, YouTube, Pinterest, Twitter/X or Facebook.");
      return;
    }
    result.hidden = true; result.innerHTML = "";
    loading.hidden = false; go.disabled = true;
    try {
      const res = await fetch("/api/info", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Something went wrong. Please try again.");
      render(url, data);
    } catch (err) {
      showError(err.message);
    } finally {
      loading.hidden = true; go.disabled = false;
    }
  });

  function render(url, data) {
    const items = data.items || [];
    if (!items.length) { showError("No downloadable media found in that post."); return; }
    let html = "";
    if (items.length > 1) {
      html += `<div class="result-head"><b>${esc(data.title)}</b><br><span>${items.length} items${data.uploader ? " · " + esc(data.uploader) : ""}</span></div>`;
    }
    for (const it of items) {
      const dur = fmtDur(it.duration);
      const opts = it.options.map((o) => {
        const href = `/api/download?url=${encodeURIComponent(url)}&option=${encodeURIComponent(o.id)}&item=${it.index}`;
        return `<a class="opt" href="${href}" data-dl><span>${esc(o.label)}</span><span class="dl">Download</span></a>`;
      }).join("");
      html += `
        <div class="card media">
          <div class="thumb">
            ${it.thumbnail ? `<img src="${esc(it.thumbnail)}" alt="" referrerpolicy="no-referrer" loading="lazy">` : ""}
            ${dur ? `<span class="badge">${dur}</span>` : it.type === "image" ? `<span class="badge">Photo</span>` : ""}
          </div>
          <div>
            <h3>${esc(items.length > 1 ? `#${it.index} · ${it.title}` : it.title)}</h3>
            ${items.length === 1 && data.uploader ? `<p class="detected" style="margin:-6px 0 12px">${esc(data.uploader)}</p>` : ""}
            <div class="opts">${opts}</div>
          </div>
        </div>`;
    }
    result.innerHTML = html;
    result.hidden = false;
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Download via fetch so we can show errors instead of a raw JSON page
  result.addEventListener("click", async (e) => {
    const a = e.target.closest("a[data-dl]");
    if (!a) return;
    e.preventDefault();
    clearError();
    a.classList.add("busy");
    try {
      const res = await fetch(a.href);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || "Download failed. Please try again.");
      }
      const blob = await res.blob();
      const cd = res.headers.get("content-disposition") || "";
      const m = cd.match(/filename\*=utf-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
      const name = m ? decodeURIComponent(m[1]) : "download";
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    } catch (err) {
      showError(err.message);
      errBox.scrollIntoView({ behavior: "smooth", block: "center" });
    } finally {
      a.classList.remove("busy");
    }
  });
})();
