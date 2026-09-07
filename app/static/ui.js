/* ─────────────────────────────────────────────────────────────
   TeamHR shared UI behaviours
   Toasts · modals/confirm · global search · keyboard shortcuts
   sidebar groups · loading helpers · focus trap
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
                    "<a class='gs-item' href='/candidates/" + c.candidate_id + "'>" +
                    "<span class='gs-primary'>" + escapeHtml(c.name || "") + "</span>" +
                    "<span class='gs-meta'>" + escapeHtml(c.status || "Draft") + " \u00B7 #" + c.candidate_id + "</span>" +
                    "<span class='gs-sub'>" + escapeHtml(c.designation || "") +
                    (c.facility_name ? " \u00B7 " + escapeHtml(c.facility_name || "") : "") + "</span></a>";
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
        function showLoading() {
            panel.innerHTML = "<div class='gs-loading'><span class='spinner spinner-sm'></span> Searching&hellip;</div>";
            openResults();
        }

        box.addEventListener("input", function () {
            var q = box.value.trim();
            clearTimeout(timer);
            if (!q) { panel.innerHTML = ""; closeResults(); return; }
            showLoading();
            timer = setTimeout(function () {
                fetch("/api/search?q=" + encodeURIComponent(q))
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        results = data || {};
                        render();
                        openResults();
                    }).catch(function () {
                        panel.innerHTML = "<div class='gs-empty'>Unable to load search results</div>";
                        openResults();
                    });
            }, 220);
        });
        box.addEventListener("focus", function () {
            if (!box.value.trim()) {
                panel.innerHTML = "<div class='gs-empty'>Type to search candidates or files</div>";
                openResults();
            } else if (panel.innerHTML) {
                openResults();
            }
        });
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

    /* ── Shared modal / confirm helpers ───────────────────── */
    // Works with both <div class="overlay" id="..."> (admin_master style) and
    // <div class="modal-overlay" id="..."> (new shared pattern).
    function openModal(id) {
        var el = document.getElementById(id);
        if (!el) return;
        el.classList.add("show");
        document.body.classList.add("modal-open");
        try {
            var focusable = el.querySelector("button, [href], input:not([type='hidden']), select, textarea, [tabindex]:not([tabindex='-1'])");
            if (focusable) setTimeout(function () { try { focusable.focus(); } catch (e) {} }, 60);
        } catch (e) {}
    }
    function closeModal(id) {
        var el = document.getElementById(id);
        if (!el) return;
        el.classList.remove("show");
        document.body.classList.remove("modal-open");
    }
    function closeAllModals() {
        document.querySelectorAll(".overlay.show, .modal-overlay.open, .drawer.open").forEach(function (el) {
            el.classList.remove("show");
            el.classList.remove("open");
        });
        document.body.classList.remove("modal-open");
    }
    window.openModal = openModal;
    window.closeModal = closeModal;

    /* Confirm dialog (replaces native confirm; returns a Promise) */
    function confirmPromise(message) {
        return new Promise(function (resolve) {
            var id = "thr-confirm-dialog";
            var existing = document.getElementById(id);
            if (existing) existing.remove();
            var overlay = document.createElement("div");
            overlay.id = id;
            overlay.className = "overlay show";
            overlay.innerHTML =
                "<div class='modal' role='dialog' aria-modal='true' aria-label='Confirm action'>" +
                "  <div class='modal-header'><h3>Confirm</h3>" +
                "    <button class='modal-close' data-action='cancel' aria-label='Close'>&times;</button>" +
                "  </div>" +
                "  <p class='confirm-message'>" + escapeHtml(message) + "</p>" +
                "  <div style='margin-top:16px;display:flex;gap:8px;justify-content:flex-end'>" +
                "    <button class='btn btn-secondary' data-action='cancel'>Cancel</button>" +
                "    <button class='btn btn-primary' data-action='confirm'>Confirm</button>" +
                "  </div>" +
                "</div>";
            document.body.appendChild(overlay);
            document.body.classList.add("modal-open");
            function finish(val) { overlay.remove(); document.body.classList.remove("modal-open"); resolve(val); }
            overlay.querySelector("[data-action='cancel']").addEventListener("click", function () { finish(false); });
            overlay.querySelector("[data-action='confirm']").addEventListener("click", function () { finish(true); });
            overlay.querySelector(".modal-close").addEventListener("click", function () { finish(false); });
            overlay.addEventListener("click", function (e) { if (e.target === overlay) finish(false); });
            setTimeout(function () {
                try { overlay.querySelector("[data-action='confirm']").focus(); } catch (e) {}
            }, 60);
        });
    }
    window.TeamHR.confirmPromise = confirmPromise;
    // Backward compat: existing pages using window.confirm will still work, but
    // new code should prefer TeamHR.confirmPromise.
    window.thrConfirm = function (message, callback) {
        confirmPromise(message).then(function (ok) { if (callback) callback(ok); });
    };

    /* ── Loading / empty / error state helpers ────────────── */
    function showLoading(el, msg) {
        if (!el) return;
        el.setAttribute("data-thr-original", el.innerHTML);
        el.innerHTML = "<div class='thr-loading-state'>" +
            "<span class='spinner'></span> <span>" + escapeHtml(msg || "Loading") + "&hellip;</span></div>";
    }
    function hideLoading(el) {
        if (!el) return;
        var orig = el.getAttribute("data-thr-original");
        if (orig != null) { el.innerHTML = orig; el.removeAttribute("data-thr-original"); }
    }
    function showEmpty(el, msg, icon) {
        if (!el) return;
        el.innerHTML = "<div class='thr-empty-state'><span class='thr-empty-ic'>" +
            (icon || "\u26AB") + "</span><span class='thr-empty-msg'>" + escapeHtml(msg || "Nothing here") +
            "</span></div>";
    }
    function showError(el, msg) {
        if (!el) return;
        el.innerHTML = "<div class='thr-error-state' role='alert'><span class='thr-err-ic'>\u26A0</span> " +
            escapeHtml(msg || "Something went wrong") + "</div>";
    }
    window.TeamHR.showLoading = showLoading;
    window.TeamHR.hideLoading = hideLoading;
    window.TeamHR.showEmpty = showEmpty;
    window.TeamHR.showError = showError;

    /* ── Keyboard shortcuts ───────────────────────────────── */
    function initShortcuts() {
        document.addEventListener("keydown", function (e) {
            var tag = (e.target && e.target.tagName) || "";
            var inField = /INPUT|TEXTAREA|SELECT/.test(tag) ||
                (e.target && e.target.isContentEditable);
            var modalOpen = document.querySelector(".overlay.show, .modal-overlay.open");

            // Esc closes any open modal/drawer (even when in field for Esc)
            if (e.key === "Escape" && modalOpen) {
                closeAllModals();
                return;
            }
            // When modal is open, block all other shortcuts
            if (modalOpen) return;
            if (inField) return;

            // "/" focuses global search
            if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) {
                e.preventDefault();
                var box = document.getElementById("globalSearchInput");
                if (box) box.focus();
                return;
            }
            // Ctrl+K focuses global search
            if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K") && !e.altKey) {
                e.preventDefault();
                var box2 = document.getElementById("globalSearchInput");
                if (box2) box2.focus();
                return;
            }
            // Alt+N / Alt+P candidate nav
            if (e.altKey) {
                if (e.key === "n" || e.key === "N") {
                    var n = document.querySelector("[data-prev-next='next']");
                    if (n) { e.preventDefault(); window.location.href = n.getAttribute("href"); }
                } else if (e.key === "p" || e.key === "P") {
                    var p = document.querySelector("[data-prev-next='prev']");
                    if (p) { e.preventDefault(); window.location.href = p.getAttribute("href"); }
                } else if (e.key === "e" || e.key === "E") {
                    // Alt+E edit candidate (candidate detail page)
                    var edit = document.querySelector("[data-action='edit']");
                    if (edit) { e.preventDefault(); edit.click(); }
                } else if (e.key === "b" || e.key === "B") {
                    // Alt+B batch review
                    e.preventDefault();
                    window.location.href = "/batch-review";
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
