(() => {
  const SITE = window.SITE || { api: "", page: "other", platforms: [], offers: [] };
  const API = (SITE.api || "").replace(/\/$/, "");

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---- offer click counting (works on every page, including offers-only pages) ----
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-offer]");
    if (!a) return;
    const url = `${API}/api/click/${encodeURIComponent(a.dataset.offer)}?page=${encodeURIComponent(a.dataset.page || SITE.page)}`;
    try {
      if (!(navigator.sendBeacon && navigator.sendBeacon(url))) fetch(url, { method: "POST", keepalive: true, mode: "no-cors" });
    } catch { /* counting must never block the visit */ }
  });

  const offerCard = (o, page) => `
    <a class="offer" href="${esc(o.url)}" target="_blank" rel="sponsored nofollow noopener" data-offer="${esc(o.id)}" data-page="${esc(page)}">
      ${o.image ? `<img class="offer-img" src="${esc(o.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}
      <div class="offer-body">
        <div class="offer-top"><b>${esc(o.title)}</b>${o.badge ? `<span class="offer-badge">${esc(o.badge)}</span>` : ""}</div>
        ${o.text ? `<p>${esc(o.text)}</p>` : ""}
        <span class="offer-btn">${esc(o.button)} ↗</span>
      </div>
    </a>`;

  // ---- downloader ----
  const $ = (id) => document.getElementById(id);
  const form = $("dl-form"), input = $("url"), go = $("go");
  const errBox = $("error"), loading = $("loading"), result = $("result"), detected = $("detected");
  if (!form) return;

  const detect = (url) => {
    try {
      const host = new URL(url.trim()).hostname.toLowerCase();
      return SITE.platforms.find((p) => p.hosts.some((h) => host === h || host.endsWith("." + h))) || null;
    } catch { return null; }
  };
  const supported = SITE.platforms.filter((p) => p.mode === "download").map((p) => p.name);
  const supportedText = supported.length > 1 ? supported.slice(0, -1).join(", ") + " or " + supported.at(-1) : supported[0] || "a supported site";

  const fmtDur = (s) => {
    if (!s) return "";
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(sec).padStart(2, "0");
  };

  const showError = (msg) => { errBox.textContent = msg; errBox.hidden = false; };
  const clearError = () => { errBox.hidden = true; errBox.textContent = ""; };

  const clearBtn = $("clear");
  input.addEventListener("input", () => {
    const p = detect(input.value);
    detected.hidden = !p;
    if (p) detected.textContent = `Detected: ${p.name}`;
    clearBtn.hidden = !input.value;
  });

  // Pasting a supported link starts the lookup straight away — no button needed
  input.addEventListener("paste", () => {
    setTimeout(() => {
      input.dispatchEvent(new Event("input"));
      if (detect(input.value)) form.requestSubmit();
    }, 0);
  });

  clearBtn.addEventListener("click", () => {
    input.value = "";
    input.dispatchEvent(new Event("input"));
    clearError();
    result.hidden = true; result.innerHTML = "";
    input.focus();
  });

  // allow ?url=... deep links
  const qs = new URLSearchParams(location.search).get("url");
  if (qs) { input.value = qs; input.dispatchEvent(new Event("input")); setTimeout(() => form.requestSubmit(), 0); }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearError();
    const url = input.value.trim();
    const p = detect(url);
    if (!p) { showError(`Paste a link from ${supportedText}.`); return; }
    result.hidden = true; result.innerHTML = "";
    if (p.mode === "offers") {
      // this platform is switched to offers in the admin panel
      showError(p.notice || `${p.name} downloads aren't available right now.`);
      if (SITE.offers.length) {
        result.innerHTML = `<div class="offers">${SITE.offers.map((o) => offerCard(o, "blocked-paste")).join("")}</div>`;
        result.hidden = false;
      }
      return;
    }
    loading.hidden = false; go.disabled = true;
    const loadingText = loading.querySelector("span");
    loadingText.textContent = "Fetching media…";
    // free hosting sleeps when idle; tell people why the first request is slow
    const slow = setTimeout(() => { loadingText.textContent = "Waking up the server — the first download can take up to a minute…"; }, 6000);
    try {
      const res = await fetch(API + "/api/info", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Something went wrong. Please try again.");
      render(url, data);
    } catch (err) {
      showError(err instanceof TypeError ? "Can't reach the download server right now. Please try again in a minute." : err.message);
      // when a download can't be done (e.g. YouTube blocking the server), still show the offers
      if (SITE.offers.length) {
        result.innerHTML = `<div class="offers-head"><span class="sponsored">Sponsored</span></div>
                            <div class="offers">${SITE.offers.map((o) => offerCard(o, "failed-download")).join("")}</div>`;
        result.hidden = false;
      }
    } finally {
      clearTimeout(slow);
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
      const link = (o) => {
        const href = `${API}/api/download?url=${encodeURIComponent(url)}&option=${encodeURIComponent(o.id)}&item=${it.index}`;
        return `<a class="opt" href="${href}" data-dl data-url="${esc(url)}" data-opt="${esc(o.id)}" data-item="${it.index}">
                  <span class="opt-label">${esc(o.label)}</span><span class="dl">Download</span>
                  <span class="opt-progress" hidden><span class="opt-bar"><i></i></span><span class="opt-status"></span></span>
                </a>`;
      };
      const vids = it.options.filter((o) => o.kind !== "audio");
      const auds = it.options.filter((o) => o.kind === "audio");
      const opts =
        (vids.length ? (auds.length ? `<p class="opt-head">Video</p>` : "") + vids.map(link).join("") : "") +
        (auds.length ? `<p class="opt-head">Audio</p>` + auds.map(link).join("") : "");
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
    if (SITE.offers.length) {
      html += `<div class="offers-head"><span class="sponsored">Sponsored</span></div>
               <div class="offers">${SITE.offers.map((o) => offerCard(o, "after-download")).join("")}</div>`;
    }
    result.innerHTML = html;
    result.hidden = false;
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---- downloads: start a job on the server, show its progress, then save the file ----
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const getJSON = async (url, opts) => {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Download failed. Please try again.");
    return data;
  };
  const saveFile = (href, name) => {
    const link = document.createElement("a");
    link.href = href;
    if (name) link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  result.addEventListener("click", async (e) => {
    const a = e.target.closest("a[data-dl]");
    if (!a) return;
    e.preventDefault();
    if (a.classList.contains("busy")) return;
    if (a.dataset.file) { saveFile(a.dataset.file, a.dataset.filename); return; }   // already done: save again

    clearError();
    const dl = a.querySelector(".dl"), box = a.querySelector(".opt-progress");
    const bar = a.querySelector(".opt-bar i"), status = a.querySelector(".opt-status");
    const show = (job) => {
      const pct = Math.round(job.percent || 0);
      bar.style.width = pct + "%";
      dl.textContent = pct + "%";
      status.textContent = [job.stage, job.speed, job.eta].filter(Boolean).join(" · ");
    };
    a.classList.add("busy"); box.hidden = false; show({ percent: 0, stage: "Starting…" });
    try {
      let job = await getJSON(`${API}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: a.dataset.url, option: a.dataset.opt, item: Number(a.dataset.item) || 0 }),
      });
      let misses = 0;
      while (job.status !== "done" && job.status !== "error") {
        show(job);
        await sleep(800);
        try { job = await getJSON(`${API}/api/jobs/${encodeURIComponent(job.id)}`); misses = 0; }
        catch (err) { if (++misses > 5 || err.message.includes("expired")) throw err; }   // ride out a blip
      }
      if (job.status === "error") throw new Error(job.error || "Download failed. Please try again.");
      show({ percent: 100, stage: "Saving to your device…" });
      const fileUrl = `${API}/api/jobs/${encodeURIComponent(job.id)}/file`;
      saveFile(fileUrl, job.filename);
      a.dataset.file = fileUrl; a.dataset.filename = job.filename || "";
      a.classList.add("done");
      dl.textContent = "Saved ✓";
      status.textContent = (job.size ? job.size + " · " : "") + "Tap again to save another copy";
    } catch (err) {
      box.hidden = true; dl.textContent = "Download";
      showError(err instanceof TypeError ? "Can't reach the download server right now. Please try again in a minute." : err.message);
      errBox.scrollIntoView({ behavior: "smooth", block: "center" });
    } finally {
      a.classList.remove("busy");
    }
  });
})();
