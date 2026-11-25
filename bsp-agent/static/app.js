// Inbox UI: lists emails, streams an agent run step by step, shows the draft.
// Approvals, edits and your own enquiries are saved on the server (SQLite).
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let emails = [];
let current = null;          // the email being shown
let products = {};           // sku -> product, filled from tool outputs during a run
let run = null;              // the run being shown: {id, controller, usage, draft, draftState}

const ICON = { search_catalogue: "🔍", get_product: "📦", get_order: "🚚", ask_customer: "❓", draft_reply: "✉️" };
const LABEL = { search_catalogue: "Searched the catalogue", get_product: "Checked a product",
  get_order: "Looked up the order", ask_customer: "Clarifying question", draft_reply: "Drafted the reply" };
const nice = (s) => String(s || "").replaceAll("_", " ");
const isEnquiry = (e) => e?.id?.startsWith("ENQ-");

// ---- Inbox ---------------------------------------------------------------
async function loadInbox() {
  const r = await fetch("/api/emails");
  const data = await r.json();
  emails = data.emails;
  $("#model").textContent = data.model;
  $("#count").textContent = `(${emails.length})`;
  $("#emailList").innerHTML = emails.map((e) => `
    <li data-id="${esc(e.id)}" class="${current?.id === e.id ? "active" : ""}">
      <div class="from"><span>${esc(e.from)}</span><span>${esc(String(e.received_at || "").slice(5, 16))}</span></div>
      <div class="subj">${esc(e.subject)}</div>
      <div class="snip">${esc(e.body)}</div>
      <div class="tags">
        ${isEnquiry(e) ? '<span class="tag intent">your enquiry</span>' : ""}
        ${e.attachment || e.has_image ? '<span class="tag">📎 photo</span>' : ""}
        ${e.status === "approved" ? '<span class="tag ok">✓ approved</span>' : e.status === "edited" ? '<span class="tag med">edited</span>' : ""}
      </div>
    </li>`).join("");
}
$("#emailList").addEventListener("click", (ev) => {
  const li = ev.target.closest("li");
  if (li) openEmail(emails.find((e) => e.id === li.dataset.id));
});

function showEmail(e) {
  $("#empty").hidden = true;
  $("#view").hidden = false;
  $("#layout").classList.add("show-detail");
  document.querySelectorAll(".email-list li").forEach((li) => li.classList.toggle("active", li.dataset.id === e.id));
  let att = "";
  if (e.has_image) att = `<div class="attach">📎 Photo uploaded<img src="/api/enquiry/${encodeURIComponent(e.id)}/image" alt="Uploaded photo"></div>`;
  else if (e.attachment) att = `<div class="attach">📎 Photo attached <span class="fine">(sample data: the agent receives this description of the photo)</span><br>${esc(e.attachment.description)}</div>`;
  $("#emailCard").innerHTML = `
    <div class="meta">${esc(e.from)} · ${esc(e.received_at)}</div>
    <h2>${esc(e.subject)}</h2>
    <div class="body">${esc(e.body)}</div>${att}`;
  $("#trace").innerHTML = "";
  $("#draft").innerHTML = "";
  $("#rerunBtn").hidden = true;
  products = {};
  $("#detail").scrollTop = 0;
}

function openEmail(e, fresh = false) {
  if (!e) return;
  current = e;
  showEmail(e);
  const r = startRun();
  streamRun(r, fetch(`/api/run/${encodeURIComponent(e.id)}${fresh ? "?fresh=1" : ""}`, { signal: r.controller.signal }));
}

// Every run gets its own AbortController. Opening another email cancels the old
// stream, and events from a cancelled run are ignored, so two runs never mix.
function startRun() {
  if (run) run.controller.abort();
  run = { id: null, controller: new AbortController(), usage: null, draft: null, draftState: null, cached: false };
  return run;
}

// ---- Streaming ---------------------------------------------------------
async function streamRun(r, responsePromise) {
  const pending = addStep({ cls: "thinking", icon: '<span class="spinner"></span>', title: "Agent is reading the email…" });
  try {
    const resp = await responsePromise;
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(typeof err.detail === "string" ? err.detail : `HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done || r !== run) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0 && r === run) {
        const line = buf.slice(0, i).replace(/^data: /, "");
        buf = buf.slice(i + 2);
        if (line.trim()) handle(JSON.parse(line), r, pending);
      }
    }
  } catch (err) {
    if (err.name === "AbortError" || r !== run) return;
    pending.remove();
    addStep({ cls: "err", icon: "!", title: "Error", body: `<div class="error-box">${esc(err.message)}</div>` });
  }
  if (r !== run) return;
  pending.remove();
  $("#rerunBtn").hidden = !r.cached;
}

function handle(ev, r, pending) {
  const add = (s) => $("#trace").insertBefore(stepEl(s), pending);
  if (ev.type === "tool_call") {
    remember(ev);
    add(stepFor(ev));
  } else if (ev.type === "text") {
    add({ cls: "thinking", icon: "💭", title: "Agent note", body: `<div class="note">${esc(ev.text)}</div>` });
  } else if (ev.type === "check") {
    add(ev.passed
      ? { icon: "✅", title: 'Checked the draft <code class="fine">verify_draft</code>', body: '<div class="summary">Every part number, price, link and order in the reply matches the data the agent looked up.</div>' }
      : { cls: "err", icon: "⚠", title: 'Checked the draft <code class="fine">verify_draft</code>', body: `<div class="summary">Found problems, so it's flagged for staff:<br>${ev.issues.map(esc).join("<br>")}</div>` });
  } else if (ev.type === "error") {
    add({ cls: "err", icon: "!", title: "Error", body: `<div class="error-box">${esc(ev.message)}</div>` });
  } else if (ev.type === "usage") {
    r.usage = ev;
  } else if (ev.type === "final") {
    r.draft = ev.draft;
    renderDraft(r);
  } else if (ev.type === "run") {
    // Saved runs announce their ID first; live runs after the draft is saved.
    r.id = ev.run_id;
    r.cached = ev.cached;
    r.draftState = ev.draft_state;
    if (r.draft) { renderDraft(r); loadInbox(); }
  } else if (ev.type === "enquiry") {
    current = ev.email;
    loadInbox();
  }
}

function remember(ev) {
  const o = ev.output || {};
  (o.results || []).forEach((p) => (products[p.sku] = { ...products[p.sku], ...p }));
  if (o.sku) products[o.sku] = { ...products[o.sku], ...o };
}

// ---- Trace steps -----------------------------------------------------------
function stepFor(ev) {
  const i = ev.input || {}, o = ev.output || {};
  let summary = "";
  if (ev.tool === "search_catalogue") {
    const f = [i.brand, nice(i.category), ...Object.entries(i.attributes || {}).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`)].filter(Boolean);
    const top = (o.results || []).slice(0, 3).map((p) => `${esc(p.part_number)}`).join(", ");
    summary = `“${esc(i.query)}”${f.length ? ` · ${esc(f.join(" · "))}` : ""}<br>${o.count ?? 0} matches${top ? `, top: ${top}` : ""}`;
  } else if (ev.tool === "get_product") {
    summary = o.error ? esc(o.error) : `${esc(o.name)} · £${o.price_inc_vat}`;
  } else if (ev.tool === "get_order") {
    const one = o.order_id ? o : (o.orders || [])[0];
    summary = o.error ? `🔒 ${esc(o.error)}` : one ? `${esc(one.order_id)} · <b>${esc(nice(one.status))}</b>${one.carrier ? ` · ${esc(one.carrier)} ${esc(one.tracking)}` : ""}${one.expected_stock_date ? ` · stock due ${esc(one.expected_stock_date)}` : ""}` : "";
    if (o.orders && o.orders.length > 1) summary += ` (+${o.orders.length - 1} more)`;
  } else if (ev.tool === "ask_customer") {
    summary = esc(i.question);
  } else if (ev.tool === "draft_reply") {
    summary = `${esc(nice(i.intent))} · confidence ${esc(i.confidence)} · urgency ${esc(i.urgency)}`;
  }
  const raw = ev.tool === "draft_reply" ? "" :
    `<details><summary>Raw tool call</summary><pre>${esc(JSON.stringify(i, null, 2))}</pre><pre>${esc(JSON.stringify(o, null, 2))}</pre></details>`;
  return { icon: ICON[ev.tool] || "•", title: `${LABEL[ev.tool] || ev.tool} <code class="fine">${ev.tool}</code>`, body: `<div class="summary">${summary}</div>${raw}` };
}

function stepEl({ cls = "", icon, title, body = "" }) {
  const li = document.createElement("li");
  li.className = `step ${cls}`;
  li.innerHTML = `<span class="dot">${icon}</span><div class="title">${title}</div>${body}`;
  return li;
}
function addStep(s) { const el = stepEl(s); $("#trace").appendChild(el); return el; }

// ---- Draft -------------------------------------------------------------
function renderDraft(r) {
  const d = r.draft, usage = r.usage, state = r.draftState;
  const conf = { high: "ok", medium: "med", low: "high" }[d.confidence] || "";
  const recs = (d.recommended_skus || []).slice(0, 3).map((sku, n) => {
    const p = products[sku];
    if (!p) return `<div class="product"><div class="rank">#${n + 1}</div><div class="pname">${esc(sku)}</div></div>`;
    return `<div class="product"><div class="rank">#${n + 1} · ${esc(p.brand)}</div>
      <div class="pname">${esc(p.name)}</div>
      <span class="price">£${Number(p.price_inc_vat).toFixed(2)}</span> <span class="fine">inc VAT</span>
      ${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noopener">view</a>` : ""}</div>`;
  }).join("");
  const checks = d.checks;
  const cost = usage ? (usage.input_tokens * 2 + usage.output_tokens * 10 + usage.cache_read_input_tokens * 0.1) / 1e6 : 0;
  $("#draft").innerHTML = `
    <div class="section-head"><h3>Draft reply</h3></div>
    <div class="card draft">
      <div class="tags">
        <span class="tag intent">${esc(nice(d.intent))}</span>
        <span class="tag ${d.urgency === "high" ? "high" : ""}">urgency: ${esc(d.urgency)}</span>
        <span class="tag ${conf}">confidence: ${esc(d.confidence)}</span>
        ${d.needs_human ? '<span class="tag human">needs staff action</span>' : '<span class="tag ok">ready to send</span>'}
        ${checks ? (checks.passed ? '<span class="tag ok">✓ facts checked</span>' : '<span class="tag high">⚠ check facts</span>') : ""}
        ${d.order_id ? `<span class="tag">order ${esc(d.order_id)}</span>` : ""}
        ${state?.status === "edited" ? '<span class="tag med">edited</span>' : ""}
      </div>
      ${d.internal_note ? `<div class="internal"><b>Note for staff:</b> ${esc(d.internal_note)}</div>` : ""}
      ${checks && !checks.passed ? `<div class="internal"><b>Fact check:</b> ${checks.issues.map(esc).join(" ")}</div>` : ""}
      ${recs ? `<div class="products">${recs}</div>` : ""}
      <textarea class="reply" id="reply" readonly>${esc(state?.body ?? d.body)}</textarea>
      <div class="actions" id="actions">
        <button class="btn ok" id="approveBtn">Approve</button>
        <button class="btn" id="editBtn">Edit</button>
        <span class="fine" id="saveMsg"></span>
      </div>
      ${usage ? `<div class="usage">${usage.api_calls} model calls · ${usage.seconds}s · ~$${cost.toFixed(4)} · ${esc(usage.model)}</div>` : ""}
    </div>`;
  if (state?.status === "approved") markApproved();
  $("#editBtn").onclick = async () => {
    const t = $("#reply");
    if (t.readOnly) {
      t.readOnly = false;
      $("#editBtn").textContent = "Save edit";
      t.focus();
    } else if (await saveDraft(r, "edited")) {
      t.readOnly = true;
      $("#editBtn").textContent = "Edit";
    }
  };
  $("#approveBtn").onclick = async () => { if (await saveDraft(r, "approved")) markApproved(); };
}

async function saveDraft(r, status) {
  const msg = $("#saveMsg");
  if (!r.id) { msg.textContent = "Still saving the run, try again in a moment."; return false; }
  const body = $("#reply").value;
  try {
    const resp = await fetch(`/api/drafts/${r.id}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status, body }) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  } catch (err) {
    msg.textContent = `Couldn't save (${err.message}).`;
    return false;
  }
  r.draftState = { status, body };
  msg.textContent = status === "edited" ? "Edit saved." : "";
  loadInbox();
  return true;
}

function markApproved() {
  $("#reply").readOnly = true;
  $("#actions").innerHTML = '<span class="approved">✓ Approved. In production this would go out through the shop\'s email system; the demo sends nothing.</span>';
}

// ---- New enquiry -------------------------------------------------------
// Phone photos are often over 5 MB, so shrink them in the browser first
// (longest side 1568 px, the size Claude's vision works best at) and send a JPEG.
async function shrinkImage(file) {
  const MAX = 1568;
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, MAX / Math.max(bmp.width, bmp.height));
    if (scale === 1 && file.size < 1.5e6 && /^image\/(jpeg|png|webp|gif)$/.test(file.type)) return file;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.85));
    return blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : file;
  } catch {
    return file;  // the browser can't decode it (e.g. HEIC on desktop); the server explains what to do
  }
}

$("#newBtn").onclick = () => $("#newDialog").showModal();
$("#backBtn").onclick = () => $("#layout").classList.remove("show-detail");
$("#rerunBtn").onclick = () => current && openEmail(current, true);
$("#newForm").image.onchange = (ev) => {
  const f = ev.target.files[0];
  const img = $("#preview");
  img.hidden = !f;
  if (f) img.src = URL.createObjectURL(f);
};
$("#newForm").addEventListener("submit", async (ev) => {
  if (ev.submitter?.value !== "send") return;
  const form = ev.target;
  const fd = new FormData(form);
  const file = form.image.files[0];
  fd.delete("image");
  if (file) fd.append("image", await shrinkImage(file));
  const e = { id: null, from: fd.get("sender") || "visitor@example.com", subject: "Website enquiry",
    received_at: "just now", body: fd.get("body"), attachment: null };
  current = e;
  showEmail(e);
  if (file) $("#emailCard").insertAdjacentHTML("beforeend",
    `<div class="attach">📎 Photo uploaded<img src="${URL.createObjectURL(file)}" alt="Uploaded photo"></div>`);
  const r = startRun();
  streamRun(r, fetch("/api/enquiry", { method: "POST", body: fd, signal: r.controller.signal }));
  form.reset();
  $("#preview").hidden = true;
});

loadInbox();
