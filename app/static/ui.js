/* ─────────────────────────────────────────────────────────────
   TeamHR shared UI behaviours
   Toasts · global search · keyboard shortcuts · sidebar groups
   Framework-agnostic; safe to load on every page.
   ───────────────────────────────────────────────────────────── */
(function () {
    "use strict";

    /* ── Toast system ─────────────────────────────────────── */
    function ensureToastContainer() {
        var c = document.getElementById("toastContainer");
        if (!c) {
            c = document.createElement("div");
            c.id = "toastContainer";
            c.className = "toast-container";
            c.setAttribute("role", "status");
            c.setAttribute("aria-live", "polite");
            document.body.appendChild(c);
        }
        return c;
    }
    var ICONS = { success: "\u2713", error: "\u2717", warning: "\u26A0", info: "\u2139" };
    function toast(msg, type, ms) {
        type = type || "info";
        var t = document.createElement("div");
        t.className = "toast toast-" + type;
        t.innerHTML = "<span class='toast-ic'>" + (ICONS[type] || "") + "</span> <span>" +
            String(msg || "").replace(/[&<>"']/g, function (c) {
                return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
            }) + "</span>";
        ensureToastContainer().appendChild(t);
        setTimeout(function () { t.remove(); }, ms || 5000);
    }
    window.TeamHR = window.TeamHR || {};
    window.TeamHR.toast = toast;
    // Also share the classic name used by existing pages if function absent.
    if (typeof window.showToast !== "function") window.showToast = toast;

    /* ── Sidebar collapsible groups ───────────────────────── */
    function initSidebarGroups() {
        document.querySelectorAll(".nav-group > .nav-group-toggle").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var g = btn.parentElement;
                var wasOpen = g.classList.contains("open");
                g.classList.toggle("open", !wasOpen);
                btn.setAttribute("aria-expanded", String(!wasOpen));
                try { localStorage.setItem("thr-sb-" + (btn.getAttribute("data-group") || "g"), wasOpen ? "0" : "1"); } catch (e) {}
            });
        });
        // Restore open state; default the first group open only if none stored.
        document.querySelectorAll(".nav-group").forEach(function (g) {
            var key = "thr-sb-" + (g.querySelector(".nav-group-toggle").getAttribute("data-group") || "g");
            var saved = null;
            try { saved = localStorage.getItem(key); } catch (e) {}
            if (saved !== null) {
                var open = saved === "1";
                g.classList.toggle("open", open);
                g.querySelector(".nav-group-toggle").setAttribute("aria-expanded", String(open));
            }
        });
    }

    /* ── Global search ────────────────────────────────────── */
    function escapeHtml(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    function initGlobalSearch() {
        var box = document.getElementById("globalSearchInput");
        if (!box) return;
        var panel = document.getElementById("globalSearchResults");
        if (!panel) return;
        var timer = null, results = [], idx = 0;

        function render() {
            panel.innerHTML = "";
            if (!results.candidates && !results.files) {
                panel.innerHTML = "<div class='gs-empty'>No matches found</div>";
                return;
            }
            if (results.candidates && results.candidates.length) {
                panel.innerHTML += "<div class='gs-group'>Candidates</div>";
                results.candidates.forEach(function (c) {
                panel.innerHTML +=
                    "<a class='gs-item' href='/manual-entry?edit=" + c.candidate_id + "'>" +
                    "<span class='gs-primary'>" + escapeHtml(c.name || "") + "</span>" +
                    "<span class='gs-meta'>" + escapeHtml(c.status || "Draft") + " \u00B7 #" + c.candidate_id + "</span></a>";
                });
            }
            if (results.files && results.files.length) {
                panel.innerHTML += "<div class='gs-group'>Generated Files</div>";
                results.files.forEach(function (f) {
                    panel.innerHTML +=
                        "<a class='gs-item' href='/batch-review?batch_id=" + f.batch_id + "'>" +
                        "<span class='gs-primary'>" + escapeHtml(f.filename) + "</span>" +
                        "<span class='gs-meta'>Batch " + f.batch_id + "</span></a>";
                });
            }
            var links = panel.querySelectorAll(".gs-item");
            idx = -1;
            links.forEach(function (l, i) { l.addEventListener("mouseenter", function () { setActive(i); }); });
        }
        function setActive(i) {
            var links = panel.querySelectorAll(".gs-item");
            if (!links.length) return;
            if (i < 0) i = 0; if (i >= links.length) i = links.length - 1;
            idx = i;
            links.forEach(function (l, k) { l.classList.toggle("active", k === idx); });
            links[idx].scrollIntoView({ block: "nearest" });
        }
        function openResults() { panel.classList.add("open"); }
        function closeResults() { panel.classList.remove("open"); }

        box.addEventListener("input", function () {
            var q = box.value.trim();
            clearTimeout(timer);
            if (!q) { panel.innerHTML = ""; closeResults(); return; }
            timer = setTimeout(function () {
                fetch("/api/search?q=" + encodeURIComponent(q))
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        results = data || {};
                        render();
                        openResults();
                    }).catch(function () { closeResults(); });
            }, 220);
        });
        box.addEventListener("focus", function () { if (panel.innerHTML) openResults(); });
        document.addEventListener("click", function (e) {
            if (!box.contains(e.target)) closeResults();
        });
        box.addEventListener("keydown", function (e) {
            var links = panel.querySelectorAll(".gs-item");
            if (!links.length) return;
            if (e.key === "ArrowDown") { e.preventDefault(); setActive(idx + 1); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive(idx - 1); }
            else if (e.key === "Enter" && idx >= 0 && links[idx]) { e.preventDefault(); window.location.href = links[idx].getAttribute("href"); }
            else if (e.key === "Escape") { closeResults(); box.blur(); }
        });
    }

    /* ── Keyboard shortcuts ───────────────────────────────── */
    function initShortcuts() {
        document.addEventListener("keydown", function (e) {
            var tag = (e.target && e.target.tagName) || "";
            var inField = /INPUT|TEXTAREA|SELECT/.test(tag) ||
                (e.target && e.target.isContentEditable);
            if (inField) return;

            // "/" focuses global search
            if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) {
                e.preventDefault();
                var box = document.getElementById("globalSearchInput");
                if (box) box.focus();
                return;
            }
            // Esc closes modals/drawers
            if (e.key === "Escape") {
                document.querySelectorAll(".modal-overlay.open, .drawer.open").forEach(function (m) {
                    m.classList.remove("open");
                    document.body.classList.remove("modal-open");
                });
            }
            // Alt+N / Alt+P candidate nav (optional hook)
            if (e.altKey) {
                if (e.key === "n" || e.key === "N") {
                    var n = document.querySelector("[data-prev-next='next']");
                    if (n) { e.preventDefault(); window.location.href = n.getAttribute("href"); }
                } else if (e.key === "p" || e.key === "P") {
                    var p = document.querySelector("[data-prev-next='prev']");
                    if (p) { e.preventDefault(); window.location.href = p.getAttribute("href"); }
                }
            }
        });
    }

    function init() {
        initSidebarGroups();
        initGlobalSearch();
        initShortcuts();
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
