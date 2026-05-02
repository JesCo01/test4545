const SKIP_WARNING_HEADER = { "ngrok-skip-browser-warning": "true" };
const REVIEW_STATES = [7, 8, 9];
const REVIEW_META = {
    7: {
        label: "Procesando",
        badge: "bg-indigo-500/10 text-indigo-300 border-indigo-500/30",
    },
    8: {
        label: "Validado",
        badge: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
    },
    9: {
        label: "Registrar",
        badge: "bg-amber-500/10 text-amber-300 border-amber-500/30",
    },
};

let allRows = [];
let searchTerm = "";
let refreshTimer = 0;
let latestCounts = {};
let latestPagination = null;

function byId(id) {
    return document.getElementById(id);
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function normalizeReview(value) {
    const num = Number(value);
    return REVIEW_STATES.includes(num) ? num : 0;
}

function extra(item) {
    return item && typeof item.extra_fields === "object" && item.extra_fields
        ? item.extra_fields
        : {};
}

function pickFirst(...values) {
    for (const value of values) {
        const text = String(value ?? "").trim();
        if (text) return text;
    }
    return "";
}

function formatList(value) {
    if (Array.isArray(value)) {
        return value.map((item) => String(item ?? "").trim()).filter(Boolean).join(", ");
    }
    return String(value ?? "").trim();
}

function formatPipeList(value) {
    if (Array.isArray(value)) {
        return value.map((item) => String(item ?? "").trim()).filter(Boolean).join("|");
    }
    return String(value ?? "")
        .split(/[|,;\n\r\t]+/)
        .map((item) => item.trim())
        .filter(Boolean)
        .join("|");
}

function isTruthy(value) {
    if (value === true) return true;
    const text = String(value ?? "").trim().toLowerCase();
    return ["1", "true", "yes", "si", "on", "x", "copied"].includes(text);
}

function isCopied(item) {
    return isTruthy(extra(item).copied);
}

function copyLineForItem(item) {
    const extras = extra(item);
    const doc = pickFirst(item?.doc, extras.doc, extras.documento, item?.username);
    const sex = pickFirst(item?.sex, extras.v4_sex, extras.sex, extras.gender);
    const altura = pickFirst(item?.altura, extras.v4_altura, extras.altura);
    const phone4 = pickFirst(item?.phone4, extras.v4_phone_last4, extras.phone_last4);
    const usernames = formatPipeList(
        item?.usernames_numeric?.length
            ? item.usernames_numeric
            : pickFirst(extras.v4_usernames, extras.usernames, extras.usernames_no_used)
    );
    const email = pickFirst(item?.generated_email, extras.v4_generated_email, extras.generated_email, extras.email);
    const password = pickFirst(item?.generated_password, extras.v4_generated_password);
    return [doc, sex, altura, phone4, usernames, email, password].join("\t");
}

async function copyText(value) {
    const text = String(value ?? "");
    if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    if (!ok) throw new Error("copy_failed");
}

function describeData(item) {
    const extras = extra(item);
    const review = normalizeReview(item?.reviewed);
    if (review === 9) {
        const addFail = pickFirst(extras.add_fail);
        if (addFail) return `add_fail: ${addFail}`;
        const pending = formatList(extras.usernames_no_used);
        if (pending) return `usernames_no_used: ${pending}`;
    }
    if (review === 7) {
        const expires = pickFirst(item?.processing_expires_at, extras.processing_expires_at);
        if (expires) return `vence: ${expires}`;
    }
    return pickFirst(
        extras.v4_review_comment,
        extras.comment,
        extras.comentario,
        extras.v4_step5_result,
        extras.v4_step5_failure_kind,
        "-"
    );
}

function describeProcessing(item) {
    const parts = [];
    if (item?.processing_owner) parts.push(`owner ${item.processing_owner}`);
    if (item?.processing_attempts) parts.push(`intentos ${item.processing_attempts}`);
    if (item?.processing_return_state !== null && item?.processing_return_state !== undefined) {
        parts.push(`vuelve ${item.processing_return_state}`);
    }
    return parts.length ? parts.join(" / ") : "-";
}

function rowSearchText(item) {
    const extras = extra(item);
    return [
        item?.id,
        item?.username,
        item?.nombre,
        item?.status,
        item?.reviewed,
        item?.generated_password,
        extras.doc,
        extras.v4_generated_email,
        extras.v4_generated_password,
        extras.generated_email,
        extras.email,
        extras.add_fail,
        formatList(extras.usernames_no_used),
        extras.v4_review_comment,
    ].join(" ").toLowerCase();
}

async function fetchReviewRows() {
    const params = new URLSearchParams({
        page: "1",
        page_size: "1000",
    });
    const response = await fetch(`/api/new/reviews?${params.toString()}`, {
        method: "GET",
        headers: SKIP_WARNING_HEADER,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok || !data || !Array.isArray(data.results)) {
        throw new Error((data && data.error) || `HTTP ${response.status}`);
    }
    latestCounts = data.counts || {};
    latestPagination = data.pagination || null;
    return data.results;
}

async function loadRows() {
    const rows = await fetchReviewRows();
    const seen = new Set();
    return rows
        .filter((item) => REVIEW_STATES.includes(normalizeReview(item?.reviewed)))
        .filter((item) => {
            const id = Number(item?.id);
            if (!Number.isFinite(id)) return true;
            if (seen.has(id)) return false;
            seen.add(id);
            return true;
        })
        .sort((a, b) => Number(b?.id || 0) - Number(a?.id || 0));
}

function setLoading(on) {
    byId("loading-state")?.classList.toggle("hidden", !on);
}

function setError(message) {
    const box = byId("error-state");
    if (!box) return;
    if (!message) {
        box.classList.add("hidden");
        box.textContent = "";
        return;
    }
    box.classList.remove("hidden");
    box.textContent = message;
}

function updateCounters(rows) {
    REVIEW_STATES.forEach((state) => {
        const serverCount = latestCounts[String(state)];
        const count = Number.isFinite(Number(serverCount))
            ? Number(serverCount)
            : rows.filter((item) => normalizeReview(item?.reviewed) === state).length;
        const el = byId(`count-${state}`);
        if (el) el.textContent = String(count);
    });
    const summary = byId("summary-text");
    if (summary) {
        const total = latestPagination?.total_items ?? rows.length;
        const suffix = total > rows.length ? ` Mostrando ${rows.length}.` : "";
        summary.textContent = `${total} resultado(s) en review 7, 8 y 9.${suffix}`;
    }
}

function renderRows() {
    const tbody = byId("results-body");
    const tableWrap = byId("table-wrap");
    const empty = byId("empty-state");
    if (!tbody || !tableWrap || !empty) return;

    const filtered = searchTerm
        ? allRows.filter((item) => rowSearchText(item).includes(searchTerm))
        : allRows.slice();

    empty.classList.toggle("hidden", filtered.length > 0);
    tableWrap.classList.toggle("hidden", filtered.length === 0);

    tbody.innerHTML = filtered.map((item) => {
        const extras = extra(item);
        const review = normalizeReview(item?.reviewed);
        const meta = REVIEW_META[review] || { label: "Otro", badge: "bg-white/5 text-zinc-300 border-white/10" };
        const doc = pickFirst(extras.doc, extras.documento, item?.username, "-");
        const nombre = pickFirst(item?.nombre, extras.nombre_completo, extras.nombre, "-");
        const email = pickFirst(item?.generated_email, extras.v4_generated_email, extras.generated_email, extras.email, "-");
        const dataText = describeData(item);
        const processing = describeProcessing(item);
        const copied = isCopied(item);
        const hasId = Number.isFinite(Number(item?.id));
        return `
            <tr class="hover:bg-white/[0.035] transition-colors">
                <td class="px-4 py-3">
                    <div class="flex items-center gap-2">
                        <label class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] hover:bg-white/[0.07] transition-colors" title="copied">
                            <input type="checkbox"
                                class="h-4 w-4 rounded border-white/20 bg-zinc-950 accent-emerald-500"
                                data-action="toggle-copied"
                                data-id="${escapeHtml(item?.id ?? "")}"
                                ${copied ? "checked" : ""}
                                ${hasId ? "" : "disabled"}>
                        </label>
                        <button type="button"
                            class="h-8 w-8 rounded-lg bg-white/5 hover:bg-emerald-500/15 text-zinc-300 hover:text-emerald-200 border border-white/10 hover:border-emerald-400/30 transition-colors flex items-center justify-center"
                            title="Copiar linea"
                            data-action="copy-line"
                            data-id="${escapeHtml(item?.id ?? "")}"
                            ${hasId ? "" : "disabled"}>
                            <i class="fa-regular fa-copy text-[11px] pointer-events-none"></i>
                        </button>
                    </div>
                </td>
                <td class="px-4 py-3">
                    <span class="inline-flex items-center gap-1.5 px-2 py-1 rounded-full border text-[10px] font-bold ${meta.badge}">
                        ${review} ${escapeHtml(meta.label)}
                    </span>
                </td>
                <td class="px-4 py-3 font-mono text-zinc-400">#${escapeHtml(item?.id ?? "-")}</td>
                <td class="px-4 py-3 font-mono text-zinc-100">${escapeHtml(doc)}</td>
                <td class="px-4 py-3 text-zinc-200 max-w-[220px] truncate" title="${escapeHtml(nombre)}">${escapeHtml(nombre)}</td>
                <td class="px-4 py-3 font-mono text-[11px] text-zinc-300 max-w-[260px] truncate" title="${escapeHtml(email)}">${escapeHtml(email)}</td>
                <td class="px-4 py-3 text-zinc-300 max-w-[320px] truncate" title="${escapeHtml(dataText)}">${escapeHtml(dataText)}</td>
                <td class="px-4 py-3 font-mono text-[11px] text-zinc-400">${escapeHtml(processing)}</td>
                <td class="px-4 py-3 font-mono text-[11px] text-zinc-500">${escapeHtml(item?.timestamp || "-")}</td>
            </tr>
        `;
    }).join("");
}

function findRowById(id) {
    const rid = Number(id);
    return allRows.find((item) => Number(item?.id) === rid) || null;
}

function setLocalCopied(id, copied) {
    const rid = Number(id);
    allRows = allRows.map((item) => {
        if (Number(item?.id) !== rid) return item;
        return {
            ...item,
            extra_fields: {
                ...extra(item),
                copied,
            },
        };
    });
}

async function saveCopied(id, copied) {
    const response = await fetch(`/api/new/reviews/${encodeURIComponent(id)}/copied`, {
        method: "POST",
        headers: {
            ...SKIP_WARNING_HEADER,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({ copied }),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok || !data || data.status !== "ok") {
        throw new Error((data && data.error) || `HTTP ${response.status}`);
    }
    setLocalCopied(id, Boolean(data.copied));
}

async function handleCopiedToggle(event) {
    const input = event.target.closest?.('[data-action="toggle-copied"]');
    if (!input) return;
    const id = input.dataset.id;
    const row = findRowById(id);
    if (!row) return;
    const previous = isCopied(row);
    const next = Boolean(input.checked);
    input.disabled = true;
    setLocalCopied(id, next);
    try {
        await saveCopied(id, next);
    } catch (error) {
        input.checked = previous;
        setLocalCopied(id, previous);
        setError(`No se pudo guardar copied: ${error?.message || error}`);
    } finally {
        input.disabled = false;
    }
}

async function handleCopyClick(event) {
    const button = event.target.closest?.('[data-action="copy-line"]');
    if (!button) return;
    const id = button.dataset.id;
    const row = findRowById(id);
    if (!row) return;
    button.disabled = true;
    try {
        await copyText(copyLineForItem(row));
        await saveCopied(id, true);
        renderRows();
    } catch (error) {
        setError(`No se pudo copiar la linea: ${error?.message || error}`);
    } finally {
        button.disabled = false;
    }
}

async function refresh() {
    const button = byId("btn-refresh");
    if (button) button.disabled = true;
    setError("");
    setLoading(true);
    try {
        allRows = await loadRows();
        updateCounters(allRows);
        renderRows();
        const updated = byId("last-updated");
        if (updated) updated.textContent = `Actualizado ${new Date().toLocaleTimeString()}`;
    } catch (error) {
        setError(`No se pudo cargar reviews 7/8/9: ${error?.message || error}`);
    } finally {
        setLoading(false);
        if (button) button.disabled = false;
    }
}

function bind() {
    byId("btn-refresh")?.addEventListener("click", refresh);
    byId("search-input")?.addEventListener("input", (event) => {
        searchTerm = String(event.target.value || "").trim().toLowerCase();
        renderRows();
    });
    byId("results-body")?.addEventListener("change", handleCopiedToggle);
    byId("results-body")?.addEventListener("click", handleCopyClick);
}

document.addEventListener("DOMContentLoaded", () => {
    bind();
    refresh();
    refreshTimer = window.setInterval(refresh, 20000);
});

window.addEventListener("beforeunload", () => {
    if (refreshTimer) window.clearInterval(refreshTimer);
});
