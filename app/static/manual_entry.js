/* ──────────────────────────────────────────────────────────────────────────
   manual_entry.js — Client-side logic for TeamHR Manual Entry
   ────────────────────────────────────────────────────────────────────────── */

(function () {
    "use strict";

    /* ── Rules Data (injected from template) ─────────────────────────────── */
    var COST_CODES   = RULES_DATA.cost_codes;
    var ALL_ROLES    = RULES_DATA.roles;
    var COST_CODE_ROLES = RULES_DATA.cost_code_roles;
    var ROLE_ALIASES = RULES_DATA.role_aliases;
    var FACILITY_TYPES    = RULES_DATA.facility_types;
    var FACILITY_TYPE_ALIASES = RULES_DATA.facility_type_aliases;
    var COST_CODE_FT = RULES_DATA.cost_code_facility_type;
    var HUB_MASTER   = RULES_DATA.hub_master;
    var LOCATION_BY_FACILITY = RULES_DATA.location_by_facility || {};

    /* ── State ───────────────────────────────────────────────────────────── */
    var selectedFile   = null;
    var selectedFileName = "";
    var batchId        = 1;
    var candidateNum   = 1;
    var batchSaved     = 0;
    var highlightedRole = -1;
    var highlightedFT   = -1;
    var highlightedCC   = -1;
    var isEditMode     = false;
    var editCandidateId = null;
    var ocrResult      = null;          /* last OCR extraction for this candidate */

    /* Multi-candidate navigation state */
    var candidates = [];           /* array of {id, number, status, data} */
    var currentIndex = -1;         /* index in candidates array for current editing */

    /* ── DOM Refs ────────────────────────────────────────────────────────── */
    var $  = function (id) { return document.getElementById(id); };

    var form             = $("candidateForm");
    var reviewScreen     = $("reviewScreen");
    var uploadZone       = $("uploadZone");
    var aadhaarFile      = $("aadhaarFile");
    var fileDisplay      = $("fileDisplay");
    var fileName         = $("fileName");
    var btnBrowse        = $("btnBrowse");
    var btnRemoveFile    = $("btnRemoveFile");

    var candidateNameInput = $("candidateNameInput");
    var candidateNameHint  = $("candidateNameHint");
    var candidateNameError = $("candidateNameError");
    var ocrStatus       = $("ocrStatus");
    var ocrStatusText   = $("ocrStatusText");
    var ocrNote         = $("ocrNote");

    var mobileInput      = $("mobileInput");
    var mobileNorm       = $("mobileNormalized");
    var mobileNormVal    = $("mobileNormValue");
    var mobileError      = $("mobileError");

    var costCodeSearch   = $("costCodeSearch");
    var costCodeValue    = $("costCodeValue");
    var costCodeList     = $("costCodeList");
    var costCodeError    = $("costCodeError");
    var derivedSummary   = $("derivedSummary");
    var derivedCC        = $("derivedCC");
    var derivedEntity    = $("derivedEntity");
    var derivedOperation = $("derivedOperation");
    var derivedTeam      = $("derivedTeam");
    var derivedFT        = $("derivedFacilityType");
    var derivedCard      = $("derivedCard");

    var roleSearch       = $("roleSearch");
    var roleValue        = $("roleValue");
    var roleList         = $("roleList");
    var roleError        = $("roleError");
    var roleHint         = $("roleHint");

    var ftAutoGroup      = $("ftAutoGroup");
    var ftSelectGroup    = $("ftSelectGroup");
    var facilityTypeAuto = $("facilityTypeAuto");
    var facilityTypeValue = $("facilityTypeValue");
    var ftSearch         = $("facilityTypeSearch");
    var ftValueSelect    = $("facilityTypeValueSelect");
    var ftList           = $("facilityTypeList");
    var ftError          = $("facilityTypeError");

    var hubSearch        = $("hubSearch");
    var hubValue         = $("hubValue");
    var hubList          = $("hubList");
    var hubHint          = $("hubHint");
    var facilityError    = $("facilityError");

    var salaryInput      = $("salaryInput");
    var salaryNorm       = $("salaryNormalized");
    var salaryNormVal    = $("salaryNormValue");
    var salaryError      = $("salaryError");

    var btnReview        = $("btnReview");
    var btnSaveDraft     = $("btnSaveDraft");
    var btnBackToEdit    = $("btnBackToEdit");
    var btnConfirmAddNext = $("btnConfirmAddNext");
    var btnConfirmFinish = $("btnConfirmFinish");

    var navBar           = $("candidateNavBar");
    var navBarCount      = $("navBarCount");
    var navBarCircles    = $("navBarCircles");
    var btnBatchOverview = $("btnBatchOverview");
    var toastContainer   = $("toastContainer");
    var recoveryBanner   = $("recoveryBanner");
    var pageTitle        = $("pageTitle");
    var pageSubtitle     = $("pageSubtitle");
    var reviewTitle      = $("reviewTitle");
    var reviewSubtitle   = $("reviewSubtitle");

    /* ── Toast Notification ──────────────────────────────────────────────── */
    function showToast(message, type) {
        type = type || "info";
        var toast = document.createElement("div");
        toast.className = "toast toast-" + type;
        var icons = { success: "\u2713", error: "\u2717", warning: "\u26A0", info: "\u2139" };
        toast.innerHTML = '<span>' + (icons[type] || "") + '</span> ' + message;
        toastContainer.appendChild(toast);
        setTimeout(function () {
            toast.style.opacity = "0";
            toast.style.transform = "translateX(100%)";
            toast.style.transition = "0.3s ease";
            setTimeout(function () { toast.remove(); }, 300);
        }, 3500);
    }

    /* ── Fuzzy Match (client-side) ───────────────────────────────────────── */
    function levenshtein(a, b) {
        var m = a.length, n = b.length;
        var dp = [];
        for (var i = 0; i <= m; i++) { dp[i] = [i]; }
        for (var j = 0; j <= n; j++) { dp[0][j] = j; }
        for (var i = 1; i <= m; i++) {
            for (var j = 1; j <= n; j++) {
                dp[i][j] = a[i - 1] === b[j - 1]
                    ? dp[i - 1][j - 1]
                    : 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
            }
        }
        return dp[m][n];
    }

    function similarity(a, b) {
        if (!a || !b) return 0;
        var maxLen = Math.max(a.length, b.length);
        if (maxLen === 0) return 1;
        return 1 - levenshtein(a, b) / maxLen;
    }

    function fuzzyFind(query, list, cutoff) {
        cutoff = cutoff || 0.55;
        var best = null, bestScore = 0;
        for (var i = 0; i < list.length; i++) {
            var score = similarity(query, list[i]);
            if (score > bestScore) { bestScore = score; best = list[i]; }
        }
        return bestScore >= cutoff ? best : null;
    }

    /* ── Searchable Dropdown Helper ──────────────────────────────────────── */
    /* opts: { clearBtn: element, arrowBtn: element }
       When clearBtn is provided, clicking it clears the selection immediately.
       When arrowBtn is provided, clicking it reopens the options list. */
    function initDropdown(containerId, inputId, listId, hiddenId, items, onSelect, opts) {
        var container = $(containerId);
        var input     = $(inputId);
        var list      = $(listId);
        var hidden    = $(hiddenId);
        var highlighted = -1;
        var opts = opts || {};
        var clearBtn  = opts.clearBtn ? $(opts.clearBtn) : null;
        var arrowBtn  = opts.arrowBtn ? $(opts.arrowBtn) : null;
        var currentValue = "";

        function render(query) {
            list.innerHTML = "";
            query = (query || "").toLowerCase();
            var filtered = items.filter(function (item) {
                if (!query) return true;
                return (item.label || "").toLowerCase().indexOf(query) !== -1 ||
                       (item.searchKey && item.searchKey.indexOf(query) !== -1);
            });
            if (filtered.length === 0) {
                list.innerHTML = '<div class="sd-option" style="cursor:default;color:var(--text-secondary)">No matches found</div>';
                list.classList.add("open");
                return;
            }
            for (var i = 0; i < filtered.length; i++) {
                var div = document.createElement("div");
                div.className = "sd-option";
                div.setAttribute("data-value", filtered[i].value);
                div.setAttribute("data-index", i);
                var html = '<div>' + filtered[i].label + '</div>';
                if (filtered[i].sub) {
                    html += '<div class="sd-option-sub">' + filtered[i].sub + '</div>';
                }
                div.innerHTML = html;
                div.addEventListener("click", (function (item) {
                    return function () {
                        input.value = item.label;
                        hidden.value = item.value;
                        currentValue = item.value;
                        list.classList.remove("open");
                        highlighted = -1;
                        if (clearBtn) clearBtn.style.display = "";
                        if (onSelect) onSelect(item.value, item);
                    };
                })(filtered[i]));
                list.appendChild(div);
            }
            highlighted = -1;
            list.classList.add("open");
        }

        function clearSelection() {
            hidden.value = "";
            input.value = "";
            currentValue = "";
            list.classList.remove("open");
            highlighted = -1;
            if (clearBtn) clearBtn.style.display = "none";
        }

        input.addEventListener("focus", function () { render(input.value); });
        input.addEventListener("input", function () { render(input.value); });

        input.addEventListener("keydown", function (e) {
            var optsList = list.querySelectorAll(".sd-option[data-value]");
            if (!optsList.length) return;
            if (e.key === "ArrowDown") {
                e.preventDefault();
                highlighted = Math.min(highlighted + 1, optsList.length - 1);
                highlightOpts(optsList, highlighted);
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                highlighted = Math.max(highlighted - 1, 0);
                highlightOpts(optsList, highlighted);
            } else if (e.key === "Enter") {
                e.preventDefault();
                if (highlighted >= 0 && optsList[highlighted]) {
                    optsList[highlighted].click();
                }
            } else if (e.key === "Escape") {
                list.classList.remove("open");
                input.blur();
            }
        });

        if (arrowBtn) {
            arrowBtn.addEventListener("mousedown", function (e) {
                e.preventDefault();
                if (list.classList.contains("open")) {
                    list.classList.remove("open");
                } else {
                    /* Reopen showing all options (not filtered by current text). */
                    render("");
                }
            });
        }

        if (clearBtn) {
            clearBtn.addEventListener("mousedown", function (e) {
                e.preventDefault();
                clearSelection();
                if (onSelect) onSelect("", null);
            });
        }

        document.addEventListener("click", function (e) {
            if (!container.contains(e.target)) {
                list.classList.remove("open");
            }
        });

        return {
            setValue: function (val) {
                hidden.value = val;
                currentValue = val;
                for (var i = 0; i < items.length; i++) {
                    if (items[i].value === val) {
                        input.value = items[i].label;
                        if (clearBtn) clearBtn.style.display = "";
                        return;
                    }
                }
                input.value = val;
                if (clearBtn) clearBtn.style.display = "";
            },
            clear: function () {
                clearSelection();
            },
            setItems: function (newItems) {
                items = newItems;
            },
            getHidden: function () { return hidden.value; },
            render: render
        };
    }

    function highlightOpts(opts, idx) {
        for (var i = 0; i < opts.length; i++) {
            opts[i].classList.remove("highlighted");
        }
        if (idx >= 0 && opts[idx]) {
            opts[idx].classList.add("highlighted");
            opts[idx].scrollIntoView({ block: "nearest" });
        }
    }

    /* ── Build Cost Code Items ───────────────────────────────────────────── */
    function buildCostCodeItems() {
        var items = [];
        var keys = Object.keys(COST_CODES);
        for (var i = 0; i < keys.length; i++) {
            var cc = COST_CODES[keys[i]];
            items.push({
                value: cc.code,
                label: cc.code + " - " + cc.entity + " - " + cc.operation,
                sub: cc.team,
                searchKey: cc.code + " " + cc.entity.toLowerCase() + " " + cc.operation.toLowerCase()
            });
        }
        return items;
    }

    /* ── Build Role Items (filtered by cost code) ───────────────────────── */
    function buildRoleItems(costCode) {
        var roles = (COST_CODE_ROLES && COST_CODE_ROLES[costCode]) || [];
        var items = [];
        for (var i = 0; i < roles.length; i++) {
            items.push({
                value: roles[i],
                label: roles[i],
                searchKey: roles[i].toLowerCase()
            });
        }
        return items;
    }

    /* ── Build Facility Type Items ───────────────────────────────────────── */
    function buildFTItems() {
        var items = [];
        for (var i = 0; i < FACILITY_TYPES.length; i++) {
            var aliases = [];
            var keys = Object.keys(FACILITY_TYPE_ALIASES);
            for (var j = 0; j < keys.length; j++) {
                if (FACILITY_TYPE_ALIASES[keys[j]] === FACILITY_TYPES[i]) {
                    aliases.push(keys[j]);
                }
            }
            items.push({
                value: FACILITY_TYPES[i],
                label: FACILITY_TYPES[i],
                searchKey: FACILITY_TYPES[i].toLowerCase() + " " + aliases.join(" ")
            });
        }
        return items;
    }

    /* ── Build Hub Items from list ───────────────────────────────────────── */
    function buildHubItems(hubList) {
        var items = [];
        for (var i = 0; i < hubList.length; i++) {
            items.push({
                value: hubList[i],
                label: hubList[i],
                searchKey: hubList[i].toLowerCase().replace(/_/g, " ").replace(/hub/g, "")
            });
        }
        return items;
    }

    /* ── Official hubs valid for a cost code (mirror of rules.py) ────────── */
    function allowedHubsForCostCode(cc) {
        var info = COST_CODES[cc];
        if (!info) return [];
        if (cc === "8752") return [];  /* Myntra FM not configured */
        if (info.entity === "Myntra") {
            return HUB_MASTER.filter(function (h) { return h.toUpperCase().indexOf("MYNTRA") !== -1; });
        }
        if (info.operation === "First Mile") {
            return HUB_MASTER.filter(function (h) { return h.endsWith("_PL") && h.toUpperCase().indexOf("MYNTRA") === -1; });
        }
        return HUB_MASTER.filter(function (h) { return !h.endsWith("_PL") && h.toUpperCase().indexOf("MYNTRA") === -1; });
    }

    /* ── Initialize Dropdowns ────────────────────────────────────────────── */
    var ccDropdown = initDropdown(
        "costCodeDropdown", "costCodeSearch", "costCodeList", "costCodeValue",
        buildCostCodeItems(),
        function (code) { onCostCodeChange(code); },
        { clearBtn: "costCodeClear", arrowBtn: "costCodeArrow" }
    );

    var roleDropdown = initDropdown(
        "roleDropdown", "roleSearch", "roleList", "roleValue",
        [],
        function () { clearFieldError("role"); }
    );

    var ftDropdown = initDropdown(
        "facilityTypeDropdown", "facilityTypeSearch", "facilityTypeList", "facilityTypeValueSelect",
        buildFTItems(),
        function () { clearFieldError("facility_type"); }
    );

    var hubDropdown = initDropdown(
        "hubDropdown", "hubSearch", "hubList", "hubValue",
        [],
        function (val) {
            clearFieldError("facility");
            var loc = (val && LOCATION_BY_FACILITY[val]) || "";
            $("locationDisplay").textContent = loc || "\u2014";
            $("locationValue").value = loc;
        }
    );

    /* ── Cost Code Change → Derived Fields + Role Filter + FT Auto + Hub ── */
    function onCostCodeChange(code) {
        var info = COST_CODES[code];
        /* Clear stale data from the previously selected cost code:
           - incompatible role
           - incompatible facility/hub
           Facility Type remains locked to "Delivery Hub" by rule. */
        roleDropdown.clear();
        hubDropdown.clear();
        roleValue.value = "";
        hubValue.value = "";
        $("locationDisplay").textContent = "\u2014";
        $("locationValue").value = "";

        if (!info) {
            derivedSummary.style.display = "none";
            derivedCard.style.display = "none";
            roleSearch.disabled = true;
            roleSearch.placeholder = "Select cost code first";
            roleHint.style.display = "";
            roleHint.textContent = "Select a cost code to enable role selection";
            hubSearch.disabled = true;
            hubSearch.placeholder = "Select cost code first";
            hubHint.style.display = "";
            hubHint.textContent = "Select a cost code to enable hub selection";
            ftAutoGroup.style.display = "none";
            ftSelectGroup.style.display = "none";
            derivedFT.style.display = "none";
            return;
        }

        /* Compact derived summary */
        derivedCC.textContent = code;
        derivedEntity.textContent = info.entity;
        derivedOperation.textContent = info.operation;
        derivedTeam.textContent = info.team;
        derivedSummary.style.display = "";

        /* Legacy card (hidden) */
        derivedCard.style.display = "none";

        /* Facility Type auto-derivation — all 4 cost codes are Delivery Hub,
           shown read-only / locked. */
        var autoFT = COST_CODE_FT[code];
        if (autoFT) {
            facilityTypeAuto.value = autoFT;
            facilityTypeValue.value = autoFT;
            ftAutoGroup.style.display = "";
            ftSelectGroup.style.display = "none";
            derivedFT.textContent = autoFT;
            derivedFT.style.display = "";
        } else {
            ftAutoGroup.style.display = "none";
            ftSelectGroup.style.display = "";
            ftDropdown.clear();
            derivedFT.style.display = "none";
        }

        /* Filter roles by cost code */
        var prefix = info.prefix;
        roleDropdown.setItems(buildRoleItems(code));
        roleSearch.disabled = false;
        roleSearch.placeholder = "Search " + prefix + " roles...";
        roleHint.style.display = "";
        roleHint.textContent = "Showing " + prefix + " roles only";

        /* Load hubs for cost code:
           4421 -> Flipkart LM
           4441 -> Flipkart FM
           8751 -> Myntra LM
           8752 -> empty (Myntra FM not configured) */
        var hubs = [];
        if (code === "8752") {
            hubs = [];
            hubDropdown.setItems([]);
            hubSearch.disabled = false;
            hubSearch.placeholder = "Myntra First Mile";
            hubHint.style.display = "";
            hubHint.textContent = "No Myntra First Mile hub master configured yet.";
            clearFieldError("cost_code");
            clearFieldError("role");
            clearFieldError("facility");
            return;
        }
        if (info.entity === "Myntra") {
            hubs = HUB_MASTER.filter(function (h) { return h.toUpperCase().indexOf("MYNTRA") !== -1; });
        } else if (info.operation === "First Mile") {
            hubs = HUB_MASTER.filter(function (h) { return h.endsWith("_PL") && h.toUpperCase().indexOf("MYNTRA") === -1; });
        } else {
            hubs = HUB_MASTER.filter(function (h) { return !h.endsWith("_PL") && h.toUpperCase().indexOf("MYNTRA") === -1; });
        }
        hubDropdown.setItems(buildHubItems(hubs));
        hubSearch.disabled = false;
        hubSearch.placeholder = "Search " + info.entity + " " + info.operation + " hubs...";
        hubHint.style.display = "";
        hubHint.textContent = hubs.length + " hubs available for " + info.entity + " " + info.operation;

        clearFieldError("cost_code");
        clearFieldError("role");
        clearFieldError("facility");
    }

    /* ── Hub Search (with fuzzy + API) ──────────────────────────────────── */
    var hubSearchTimer = null;
    hubSearch.addEventListener("input", function () {
        facilityError.style.display = "none";
        var query = hubSearch.value.trim().toLowerCase();
        var ccCode = costCodeValue.value;
        if (!ccCode) return;
        var info = COST_CODES[ccCode];
        if (!info) return;

        /* 8752 -> no hub master configured; no suggestions */
        if (ccCode === "8752") {
            hubDropdown.setItems([{ value: "", label: "No Myntra First Mile hub master configured yet", searchKey: "" }]);
            hubDropdown.render("");
            hubHint.textContent = "No Myntra First Mile hub master configured yet.";
            return;
        }

        /* Client-side fuzzy filter from HUB_MASTER */
        var hubs;
        if (info.entity === "Myntra") {
            hubs = HUB_MASTER.filter(function (h) { return h.toUpperCase().indexOf("MYNTRA") !== -1; });
        } else if (info.operation === "First Mile") {
            hubs = HUB_MASTER.filter(function (h) { return h.endsWith("_PL") && h.toUpperCase().indexOf("MYNTRA") === -1; });
        } else {
            hubs = HUB_MASTER.filter(function (h) { return !h.endsWith("_PL") && h.toUpperCase().indexOf("MYNTRA") === -1; });
        }

        if (!query) {
            hubDropdown.setItems(buildHubItems(hubs));
            hubDropdown.render("");
            return;
        }

        /* Fuzzy match on hub names */
        var scored = [];
        for (var i = 0; i < hubs.length; i++) {
            var hubLower = hubs[i].toLowerCase();
            var hubClean = hubLower.replace(/hub/g, "").replace(/_/g, "").replace(/blr/g, "").replace(/pl/g, "").trim();
            var qTokens = query.split(/[\s_\-]+/);
            var hTokens = hubClean.split(/[\s_\-]+/);
            var tokenScore = 0;
            for (var t = 0; t < qTokens.length; t++) {
                for (var h = 0; h < hTokens.length; h++) {
                    if (hTokens[h].indexOf(qTokens[t]) !== -1 || qTokens[t].indexOf(hTokens[h]) !== -1) {
                        tokenScore++;
                        break;
                    }
                }
            }
            var ratio = tokenScore / Math.max(qTokens.length, 1);
            if (ratio < 0.5) {
                ratio = similarity(query, hubClean);
            }
            if (hubLower.indexOf(query) !== -1) ratio = Math.max(ratio, 1.0);
            if (ratio >= 0.35) {
                scored.push({ hub: hubs[i], score: ratio });
            }
        }
        scored.sort(function (a, b) { return b.score - a.score; });
        var matches = scored.slice(0, 5).map(function (s) { return s.hub; });

        if (matches.length === 0) {
            hubDropdown.setItems([{ value: "", label: "No matching hub found", searchKey: "" }]);
        } else {
            hubDropdown.setItems(buildHubItems(matches));
        }
        hubDropdown.render(query);
    });

    /* ── Role Search (with alias + fuzzy support) ────────────────────────── */
    roleSearch.addEventListener("input", function () {
        var query = roleSearch.value.trim().toLowerCase();
        var ccCode = costCodeValue.value;
        var info = COST_CODES[ccCode];
        if (!info) return;
        var prefix = info.prefix;
        var available = (COST_CODE_ROLES && COST_CODE_ROLES[ccCode]) || [];

        /* Prexo not available for Myntra Last Mile (8751) */
        if (ccCode === "8751" && query.indexOf("prexo") !== -1) {
            roleError.textContent = "Prexo Delivery Executive is not available for Myntra Last Mile.";
            roleError.style.display = "";
            roleDropdown.setItems([{ value: "", label: "Prexo not available for Myntra Last Mile", searchKey: "" }]);
            roleDropdown.render(query);
            return;
        } else {
            roleError.style.display = "none";
        }

        if (!query) {
            roleDropdown.setItems(buildRoleItems(ccCode));
            roleDropdown.render("");
            return;
        }

        /* 1. Check alias */
        var baseRole = ROLE_ALIASES[query];
        var items = [];
        if (baseRole) {
            var full = prefix + " - " + baseRole;
            for (var i = 0; i < available.length; i++) {
                if (available[i].toLowerCase() === full.toLowerCase()) {
                    items.push({ value: available[i], label: available[i], searchKey: available[i].toLowerCase() });
                }
            }
        }

        /* 2. Substring match */
        if (items.length === 0) {
            for (var i = 0; i < available.length; i++) {
                if (available[i].toLowerCase().indexOf(query) !== -1) {
                    items.push({ value: available[i], label: available[i], searchKey: available[i].toLowerCase() });
                }
            }
        }

        /* 3. Fuzzy match on alias keys */
        if (items.length === 0) {
            var aliasKeys = Object.keys(ROLE_ALIASES);
            var fuzzyAlias = fuzzyFind(query, aliasKeys);
            if (fuzzyAlias) {
                var baseRole2 = ROLE_ALIASES[fuzzyAlias];
                var full2 = prefix + " - " + baseRole2;
                for (var i = 0; i < available.length; i++) {
                    if (available[i].toLowerCase() === full2.toLowerCase()) {
                        items.push({ value: available[i], label: available[i], searchKey: available[i].toLowerCase() });
                    }
                }
            }
        }

        /* 4. Fuzzy match on role names */
        if (items.length === 0) {
            var roleNames = available.map(function (r) { return r.toLowerCase(); });
            var fuzzyRole = fuzzyFind(query, roleNames, 0.45);
            if (fuzzyRole) {
                for (var i = 0; i < available.length; i++) {
                    if (available[i].toLowerCase() === fuzzyRole) {
                        items.push({ value: available[i], label: available[i], searchKey: available[i].toLowerCase() });
                    }
                }
            }
        }

        if (items.length === 0) {
            items.push({ value: "", label: "No matching role found", searchKey: "" });
        }

        roleDropdown.setItems(items);
        roleDropdown.render(query);
    });

    /* ── File Upload ─────────────────────────────────────────────────────── */
    btnBrowse.addEventListener("click", function (e) {
        e.stopPropagation();
        aadhaarFile.click();
    });

    uploadZone.addEventListener("click", function () {
        aadhaarFile.click();
    });

    uploadZone.addEventListener("dragover", function (e) {
        e.preventDefault();
        uploadZone.classList.add("dragover");
    });
    uploadZone.addEventListener("dragleave", function () {
        uploadZone.classList.remove("dragover");
    });
    uploadZone.addEventListener("drop", function (e) {
        e.preventDefault();
        uploadZone.classList.remove("dragover");
        if (e.dataTransfer.files.length) {
            handleFile(e.dataTransfer.files[0]);
        }
    });

    aadhaarFile.addEventListener("change", function () {
        if (aadhaarFile.files.length) {
            handleFile(aadhaarFile.files[0]);
        }
    });

    function handleFile(file) {
        var allowed = [".jpg", ".jpeg", ".png", ".pdf"];
        var ext = "." + file.name.split(".").pop().toLowerCase();
        if (allowed.indexOf(ext) === -1) {
            showToast("Unsupported file type. Use JPG, JPEG, PNG, or PDF.", "error");
            return;
        }
        selectedFile = file;
        selectedFileName = file.name;
        fileName.textContent = file.name;
        fileDisplay.style.display = "flex";
        uploadZone.style.display = "none";
        showToast("File attached: " + file.name, "success");
        runManualOcr(file);
    }

    /* ── OCR via shared backend service ──────────────────────────────────── */
    function runManualOcr(file) {
        ocrStatus.style.display = "";
        ocrStatusText.textContent = "Reading Aadhaar...";
        ocrNote.textContent = "";

        var fd = new FormData();
        fd.append("file", file, file.name);

        fetch("/api/manual-ocr-extract", { method: "POST", body: fd })
            .then(function (r) { return r.json(); })
            .then(function (res) {
                ocrResult = res;
                var nameObj = res.name || {};
                var nameVal = nameObj.value || "";
                ocrStatus.style.display = "none";

                if (nameVal && nameVal.trim().toLowerCase() !== "male" &&
                    nameVal.trim().toLowerCase() !== "female") {
                    candidateNameInput.value = nameVal;
                    candidateNameHint.textContent = "Name auto-read from Aadhaar. Source: " + (nameObj.source || "Aadhaar");
                    showToast("Candidate name populated from Aadhaar OCR.", "success");
                } else {
                    candidateNameInput.value = "";
                    candidateNameHint.textContent = "Could not confidently read candidate name. Please enter it manually.";
                    showToast("Could not confidently read candidate name. Please enter it manually.", "warning");
                }

                /* Populate other OCR-derived fields */
                if (res.aadhaar_masked && res.aadhaar_masked !== "XXXX XXXX ?") {
                    ocrNote.textContent = "Aadhaar number detected.";
                }
                if (res.dob && res.dob.value) {
                    ocrNote.textContent = (ocrNote.textContent ? ocrNote.textContent + " " : "") + "DOB: " + res.dob.value;
                }
                if (res.address && res.address.value) {
                    ocrNote.textContent = (ocrNote.textContent ? ocrNote.textContent + " " : "") + "Address detected.";
                }

                scheduleAutoSave();
            })
            .catch(function () {
                ocrStatus.style.display = "none";
                candidateNameHint.textContent = "Could not confidently read candidate name. Please enter it manually.";
                showToast("OCR failed. You can still enter details manually.", "warning");
            });
    }

    btnRemoveFile.addEventListener("click", function (e) {
        e.stopPropagation();
        selectedFile = null;
        selectedFileName = "";
        aadhaarFile.value = "";
        fileDisplay.style.display = "none";
        uploadZone.style.display = "";
        ocrStatus.style.display = "none";
        ocrNote.textContent = "Upload an Aadhaar file to auto-extract name, DOB, number, and address.";
        ocrResult = null;
    });

    /* ── Mobile Normalization ────────────────────────────────────────────── */
    function normalizeMobile(raw) {
        var digits = raw.replace(/\D/g, "");
        if (digits.length === 12 && digits.indexOf("91") === 0) {
            digits = digits.substring(2);
        }
        return digits;
    }

    mobileInput.addEventListener("blur", function () {
        var raw = mobileInput.value.trim();
        if (!raw) {
        mobileNorm.style.display = "none";
        mobileInput.classList.remove("input-valid", "input-error");
        mobileError.style.display = "none";

        candidateNameInput.value = "";
        candidateNameInput.classList.remove("input-valid", "input-error");
        candidateNameError.style.display = "none";
        candidateNameHint.textContent = "Upload Aadhaar to auto-detect name";
        ocrStatus.style.display = "none";
        ocrNote.textContent = "Upload an Aadhaar file to auto-extract name, DOB, number, and address.";
        ocrResult = null;
            return;
        }
        var digits = normalizeMobile(raw);
        if (digits.length === 10 && "6789".indexOf(digits[0]) !== -1) {
            mobileInput.value = digits;
            mobileNormVal.textContent = digits;
            mobileNorm.style.display = "";
            mobileInput.classList.remove("input-error");
            mobileInput.classList.add("input-valid");
            mobileError.style.display = "none";
        } else {
            mobileInput.classList.remove("input-valid");
            mobileInput.classList.add("input-error");
            mobileError.textContent = "Enter a valid 10-digit Indian mobile number";
            mobileError.style.display = "";
            mobileNorm.style.display = "none";
        }
    });

    mobileInput.addEventListener("input", function () {
        if (mobileInput.classList.contains("input-error")) {
            mobileInput.classList.remove("input-error");
            mobileError.style.display = "none";
        }
    });

    /* ── Salary Normalization ────────────────────────────────────────────── */
    function normalizeSalary(raw) {
        raw = raw.trim().toLowerCase().replace(/,/g, "");
        if (!raw) return null;
        if (raw.endsWith("k")) {
            var num = parseFloat(raw.substring(0, raw.length - 1));
            if (!isNaN(num)) return Math.round(num * 1000);
            return null;
        }
        var val = parseFloat(raw);
        return isNaN(val) ? null : Math.round(val);
    }

    salaryInput.addEventListener("blur", function () {
        var raw = salaryInput.value.trim();
        if (!raw) {
            salaryNorm.style.display = "none";
            salaryInput.classList.remove("input-valid", "input-error");
            salaryError.style.display = "none";
            return;
        }
        var val = normalizeSalary(raw);
        if (val !== null && val > 0) {
            salaryNormVal.textContent = "\u20B9" + val.toLocaleString("en-IN");
            salaryNorm.style.display = "";
            salaryInput.classList.remove("input-error");
            salaryInput.classList.add("input-valid");
            salaryError.style.display = "none";
        } else {
            salaryInput.classList.remove("input-valid");
            salaryInput.classList.add("input-error");
            salaryError.textContent = "Invalid salary format";
            salaryError.style.display = "";
            salaryNorm.style.display = "none";
        }
    });

    salaryInput.addEventListener("input", function () {
        if (salaryInput.classList.contains("input-error")) {
            salaryInput.classList.remove("input-error");
            salaryError.style.display = "none";
        }
    });

    /* ── Facility Type Error Clear ───────────────────────────────────────── */
    ftSearch.addEventListener("input", function () {
        ftError.style.display = "none";
    });

    /* ── Hub Input Clear ─────────────────────────────────────────────────── */
    hubSearch.addEventListener("input", function () {
        facilityError.style.display = "none";
    });

    /* ── Clear Field Error Helper ────────────────────────────────────────── */
    function clearFieldError(field) {
        var el = $(field + "Error");
        if (el) el.style.display = "none";
    }

    /* ── Validation ──────────────────────────────────────────────────────── */
    function validateForm() {
        var errors = {};

        /* Candidate Name (required) */
        var name = candidateNameInput.value.trim();
        if (!name) {
            errors.name = "Candidate Name is required";
            candidateNameInput.classList.add("input-error");
        } else if (name.toLowerCase() === "male" || name.toLowerCase() === "female") {
            errors.name = "Please enter the candidate's actual name (not MALE/FEMALE)";
            candidateNameInput.classList.add("input-error");
        } else {
            candidateNameInput.classList.remove("input-error");
            candidateNameInput.classList.add("input-valid");
        }

        /* Mobile */
        var mobile = mobileInput.value.trim();
        var mobileDigits = normalizeMobile(mobile);
        if (!mobile || mobileDigits.length !== 10 || "6789".indexOf(mobileDigits[0]) === -1) {
            errors.mobile = "Enter a valid 10-digit Indian mobile number";
            mobileInput.classList.add("input-error");
        } else {
            mobileInput.classList.remove("input-error");
            mobileInput.classList.add("input-valid");
        }

        /* Cost Code */
        var cc = costCodeValue.value;
        if (!cc || !COST_CODES[cc]) {
            errors.cost_code = "Please select a valid cost code";
        }

        /* Role */
        var role = roleValue.value;
        if (!role) {
            errors.role = "Role is required";
        } else if (cc === "8751" && role.toLowerCase().indexOf("prexo") !== -1) {
            errors.role = "Prexo Delivery Executive is not available for Myntra Last Mile.";
        }

        /* Facility Type (only if not auto-derived) */
        var autoFT = COST_CODE_FT[cc];
        if (!autoFT) {
            var ft = ftValueSelect.value;
            if (!ft) {
                errors.facility_type = "Facility type is required";
            }
        }

        /* Facility / Hub - must be an official hub valid for the cost code */
        var fac = hubValue.value.trim();
        if (cc === "8752") {
            errors.facility = "No Myntra First Mile hub master configured yet.";
        } else {
            var allowedHubs = allowedHubsForCostCode(cc);
            if (!fac) {
                errors.facility = "Facility / Hub is required";
            } else if (allowedHubs.length && allowedHubs.indexOf(fac) === -1) {
                errors.facility = '"' + fac + '" is not a valid hub for cost code ' + cc + ". Please select an official hub from the list.";
            }
        }

        /* Salary */
        var sal = normalizeSalary(salaryInput.value);
        if (sal === null || sal <= 0) {
            errors.salary = "Valid salary is required";
        }

        /* Display errors */
        var errorMap = {
            "name": "candidateNameError",
            "mobile": "mobileError",
            "cost_code": "costCodeError",
            "role": "roleError",
            "facility_type": "facilityTypeError",
            "facility": "facilityError",
            "salary": "salaryError"
        };
        var fields = ["name", "mobile", "cost_code", "role", "facility_type", "facility", "salary"];
        for (var i = 0; i < fields.length; i++) {
            var f = fields[i];
            var errEl = $(errorMap[f]) || $(f + "Error") || $(f.replace("_", "") + "Error");
            if (errors[f]) {
                if (errEl) {
                    errEl.textContent = errors[f];
                    errEl.style.display = "";
                }
            } else {
                if (errEl) errEl.style.display = "none";
            }
        }

        return Object.keys(errors).length === 0;
    }

    /* ── Collect Form Data ───────────────────────────────────────────────── */
    function collectData() {
        var cc = costCodeValue.value;
        var info = COST_CODES[cc] || {};
        var autoFT = COST_CODE_FT[cc];
        var ftValue;
        if (autoFT) {
            ftValue = autoFT;
        } else {
            ftValue = ftValueSelect.value;
        }
        return {
            batch_id: batchId,
            candidate_number: candidateNum,
            name: candidateNameInput.value.trim(),
            mobile: normalizeMobile(mobileInput.value.trim()),
            aadhaar_filename: selectedFileName,
            cost_code: cc,
            entity: info.entity || "",
            operation: info.operation || "",
            team: info.team || "",
            role: roleValue.value,
            facility_type: ftValue,
            facility: hubValue.value,
            salary: normalizeSalary(salaryInput.value),
            salary_display: salaryInput.value.trim(),
            aadhaar_number: (ocrResult && ocrResult.aadhaar_number) || (EDIT_CANDIDATE && EDIT_CANDIDATE.aadhaar_number) || "",
            dob: (ocrResult && ocrResult.dob && ocrResult.dob.value) || "",
            address: (ocrResult && ocrResult.address && ocrResult.address.value) || "",
            migrant: "No",
            edit_id: editCandidateId
        };
    }

    /* ── Populate Review Screen ──────────────────────────────────────────── */
    function showReview() {
        var data = collectData();
        $("rvName").textContent = data.name || "\u2014";
        $("rvAadhaar").textContent = data.aadhaar_filename || "Not uploaded";
        var fullAadhaar = data.aadhaar_number || (EDIT_CANDIDATE && EDIT_CANDIDATE.aadhaar_number) || "";
        $("rvAadhaarNum").textContent = fullAadhaar || "XXXX XXXX ?";
        $("rvDob").textContent = data.dob || "\u2014";
        $("rvMobile").textContent = data.mobile || "\u2014";
        $("rvCostCode").textContent = data.cost_code ? data.cost_code + " - " + data.entity + " - " + data.operation : "\u2014";
        $("rvEntity").textContent = data.entity || "\u2014";
        $("rvOperation").textContent = data.operation || "\u2014";
        $("rvTeam").textContent = data.team || "\u2014";
        $("rvRole").textContent = data.role || "\u2014";
        $("rvFacilityType").textContent = data.facility_type || "\u2014";
        $("rvFacility").textContent = data.facility || "\u2014";
        $("rvSalary").textContent = data.salary ? "\u20B9" + data.salary.toLocaleString("en-IN") : "\u2014";
        $("rvMigrant").textContent = "No";
        $("rvAddress").textContent = data.address || "\u2014";

        if (isEditMode) {
            reviewTitle.textContent = "Edit Candidate #" + candidateNum;
            reviewSubtitle.textContent = "Verify all details before saving changes";
        } else {
            reviewTitle.textContent = "Review Candidate";
            reviewSubtitle.textContent = "Verify all details before confirming";
        }

        form.style.display = "none";
        reviewScreen.style.display = "";
    }

    function hideReview() {
        reviewScreen.style.display = "none";
        form.style.display = "";
    }

    /* ── Duplicate Check ─────────────────────────────────────────────────── */
    function checkDuplicate(mobile, excludeId) {
        return fetch("/api/check-duplicate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mobile: mobile, batch_id: batchId, exclude_id: excludeId })
        }).then(function (r) { return r.json(); });
    }

    /* ── Duplicate Confirmation Modal ────────────────────────────────────── */
    var duplicateModalEl = document.getElementById("duplicateModal");
    var dupViewBtn = document.getElementById("dupViewExisting");
    var dupContinueBtn = document.getElementById("dupContinue");
    var dupPendingMobile = null;

    function showDuplicateModal(existing) {
        var box = document.getElementById("dupDetails");
        box.innerHTML = "";
        var fields = [
            ["Name", existing && existing.name],
            ["Mobile", existing && existing.mobile],
            ["Cost Code", existing && existing.cost_code],
            ["Facility", existing && existing.facility],
            ["Status", existing && existing.status],
            ["Created", existing && existing.created_date]
        ];
        fields.forEach(function (f) {
            var label = document.createElement("span");
            label.className = "detail-label";
            label.textContent = f[0];
            var value = document.createElement("span");
            value.className = "detail-value";
            value.textContent = (f[1] || "\u2014");
            box.appendChild(label);
            box.appendChild(value);
        });
        dupPendingMobile = existing && existing.mobile;
        duplicateModalEl.style.display = "flex";
    }

    function hideDuplicateModal() {
        duplicateModalEl.style.display = "none";
    }

    dupViewBtn.addEventListener("click", function () {
        hideDuplicateModal();
        var q = encodeURIComponent(dupPendingMobile || "");
        window.location.href = "/candidates?search=" + q;
    });

    dupContinueBtn.addEventListener("click", function () {
        hideDuplicateModal();
        showReview();
        clearLocalDraft();
    });

    duplicateModalEl.addEventListener("click", function (e) {
        if (e.target === duplicateModalEl) {
            hideDuplicateModal();
        }
    });

    /* ── Save Draft ──────────────────────────────────────────────────────── */
    function saveDraft() {
        var data = collectData();
        return fetch("/api/save-draft", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        }).then(function (r) { return r.json(); });
    }

    /* ── Confirm Candidate ───────────────────────────────────────────────── */
    function confirmCandidate() {
        var data = collectData();
        return fetch("/api/confirm-candidate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        }).then(function (r) { return r.json(); });
    }

    /* ── Multi-Candidate Navigation ──────────────────────────────────────── */
    function updateNavBar() {
        navBarCount.textContent = candidates.length;
        navBarCircles.innerHTML = "";

        if (candidates.length === 0) {
            btnBatchOverview.style.display = "none";
            return;
        }

        btnBatchOverview.style.display = "";

        for (var i = 0; i < candidates.length; i++) {
            var c = candidates[i];
            var circle = document.createElement("div");
            circle.className = "nav-circle";
            if (i === currentIndex) {
                circle.classList.add("active");
            } else if (c.status === "ready") {
                circle.classList.add("done");
            } else if (c.status === "draft") {
                circle.classList.add("draft");
            }
            circle.textContent = c.number;
            circle.setAttribute("data-index", i);
            circle.addEventListener("click", function () {
                var idx = parseInt(this.getAttribute("data-index"), 10);
                navigateToCandidate(idx);
            });
            navBarCircles.appendChild(circle);
        }

        /* Current editing dot */
        if (currentIndex >= 0 && candidates[currentIndex].status !== "ready") {
            var activeDot = document.createElement("div");
            activeDot.className = "nav-circle active";
            activeDot.textContent = "\u270E";
            activeDot.style.fontSize = "0.65rem";
            navBarCircles.appendChild(activeDot);
        }
    }

    function navigateToCandidate(index) {
        if (index === currentIndex) return;
        if (index < 0 || index >= candidates.length) return;

        /* Save current draft if unfinished */
        saveCurrentDraft();

        /* Switch to target candidate */
        currentIndex = index;
        var c = candidates[index];
        candidateNum = c.number;

        /* Restore form from saved data */
        restoreFormFromData(c.data);

        updateNavBar();
        updatePageTitle();
        hideReview();
        showToast("Switched to Candidate " + c.number, "info");
    }

    function saveCurrentDraft() {
        if (currentIndex < 0) return;
        var c = candidates[currentIndex];
        if (c.status === "ready") return;

        /* Update the data snapshot */
        c.data = snapshotFormData();
    }

    function snapshotFormData() {
        return {
            candidate_name: candidateNameInput.value,
            mobile: mobileInput.value,
            cost_code: costCodeValue.value,
            role: roleValue.value,
            facility_type: COST_CODE_FT[costCodeValue.value] || ftValueSelect.value,
            facility: hubValue.value,
            location_code: $("locationValue").value,
            salary: salaryInput.value,
            aadhaar_filename: selectedFileName,
            candidateNum: candidateNum,
            batchId: batchId
        };
    }

    function restoreFormFromData(data) {
        /* Reset form first */
        form.reset();
        selectedFile = null;
        selectedFileName = "";
        aadhaarFile.value = "";
        fileDisplay.style.display = "none";
        uploadZone.style.display = "";

        mobileNorm.style.display = "none";
        mobileInput.classList.remove("input-valid", "input-error");
        mobileError.style.display = "none";

        ccDropdown.clear();
        derivedSummary.style.display = "none";
        derivedCard.style.display = "none";

        roleDropdown.clear();
        roleDropdown.setItems([]);
        roleSearch.disabled = true;
        roleSearch.placeholder = "Select cost code first";
        roleHint.style.display = "";
        roleHint.textContent = "Select a cost code to enable role selection";
        roleError.style.display = "none";

        ftAutoGroup.style.display = "none";
        ftSelectGroup.style.display = "none";
        ftError.style.display = "none";

        hubDropdown.clear();
        hubSearch.disabled = true;
        hubSearch.placeholder = "Select cost code first";
        hubHint.style.display = "";
        hubHint.textContent = "Select a cost code to enable hub selection";
        facilityError.style.display = "none";

        salaryNorm.style.display = "none";
        salaryInput.classList.remove("input-valid", "input-error");
        salaryError.style.display = "none";

        /* Restore values */
        if (data.candidate_name) {
            candidateNameInput.value = data.candidate_name;
            candidateNameHint.textContent = "Restored from saved draft.";
        }
        if (data.mobile) mobileInput.value = data.mobile;
        if (data.salary) salaryInput.value = data.salary;
        if (data.aadhaar_filename) {
            selectedFileName = data.aadhaar_filename;
            fileName.textContent = data.aadhaar_filename;
            fileDisplay.style.display = "flex";
            uploadZone.style.display = "none";
        }
        if (data.cost_code) {
            ccDropdown.setValue(data.cost_code);
            onCostCodeChange(data.cost_code);
            if (data.role) {
                setTimeout(function () { roleDropdown.setValue(data.role); }, 100);
            }
            if (data.facility_type && !COST_CODE_FT[data.cost_code]) {
                setTimeout(function () { ftDropdown.setValue(data.facility_type); }, 100);
            }
            if (data.facility) {
                setTimeout(function () {
                    hubDropdown.setValue(data.facility);
                    var loc = LOCATION_BY_FACILITY[data.facility] || "";
                    $("locationDisplay").textContent = loc || "\u2014";
                    $("locationValue").value = loc;
                }, 100);
            }
        }

        /* Trigger normalization displays */
        mobileInput.dispatchEvent(new Event("blur"));
        salaryInput.dispatchEvent(new Event("blur"));
    }

    function updatePageTitle() {
        if (isEditMode) {
            pageTitle.textContent = "Edit Candidate #" + candidateNum;
            pageSubtitle.textContent = "Editing existing candidate. Changes will update the existing record.";
        } else {
            pageTitle.textContent = "New Candidate";
            pageSubtitle.textContent = "Enter candidate details manually. Smart validation will help prevent onboarding errors.";
        }
    }

    /* ── Batch Progress UI ───────────────────────────────────────────────── */
    function updateBatchProgress() {
        updateNavBar();
    }

    /* ── Reset Form ──────────────────────────────────────────────────────── */
    function resetForm() {
        form.reset();
        selectedFile = null;
        selectedFileName = "";
        aadhaarFile.value = "";
        fileDisplay.style.display = "none";
        uploadZone.style.display = "";

        mobileNorm.style.display = "none";
        mobileInput.classList.remove("input-valid", "input-error");
        mobileError.style.display = "none";
        candidateNameInput.value = "";
        candidateNameInput.classList.remove("input-valid", "input-error");
        candidateNameError.style.display = "none";
        candidateNameHint.textContent = "Upload Aadhaar to auto-detect name";
        ocrStatus.style.display = "none";
        ocrNote.textContent = "Upload an Aadhaar file to auto-extract name, DOB, number, and address.";
        ocrResult = null;

        ccDropdown.clear();
        derivedSummary.style.display = "none";
        derivedCard.style.display = "none";

        roleDropdown.clear();
        roleDropdown.setItems([]);
        roleSearch.disabled = true;
        roleSearch.placeholder = "Select cost code first";
        roleHint.style.display = "";
        roleHint.textContent = "Select a cost code to enable role selection";
        roleError.style.display = "none";

        ftAutoGroup.style.display = "none";
        ftSelectGroup.style.display = "none";
        ftError.style.display = "none";
        facilityTypeAuto.value = "";
        facilityTypeValue.value = "";
        derivedFT.style.display = "none";

        hubDropdown.clear();
        hubSearch.disabled = true;
        hubSearch.placeholder = "Select cost code first";
        hubHint.style.display = "";
        hubHint.textContent = "Select a cost code to enable hub selection";
        facilityError.style.display = "none";

        salaryNorm.style.display = "none";
        salaryInput.classList.remove("input-valid", "input-error");
        salaryError.style.display = "none";

        isEditMode = false;
        editCandidateId = null;
        updatePageTitle();
        hideReview();
    }

    /* ── Recovery Draft (server-side, non-sensitive) ────────────────────────
       Parallel to localStorage. Persists only safe, non-sensitive form fields
       to the server so an unfinished form can be recovered across sessions.
       Aadhaar_number / address are NEVER stored by this draft layer.      */

    var DRAFT_KEY = "teamhr_draft";
    var SERVER_DRAFT_ID = null;

    function snapshotSafeFormData() {
        if (typeof snapshotFormData === "function") return snapshotFormData();
        return {};
    }

    function saveLocalDraft() {
        var data = snapshotFormData();
        try {
            localStorage.setItem(DRAFT_KEY, JSON.stringify(data));
        } catch (e) { /* quota exceeded, ignore */ }
    }

    function saveServerDraft() {
        var data = snapshotSafeFormData();
        var hasData = data && (data.candidate_name || data.mobile || data.cost_code ||
                               data.facility || data.salary);
        if (!hasData) return;
        var body = {
            draft_type: "manual_entry",
            safe_payload: data,
            draft_id: SERVER_DRAFT_ID
        };
        fetch("/api/drafts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        })
            .then(function (r) { return r.json(); })
            .then(function (res) {
                if (res && res.draft_id) SERVER_DRAFT_ID = res.draft_id;
            })
            .catch(function () { /* offline / non-fatal */ });
    }

    function clearServerDraft() {
        if (!SERVER_DRAFT_ID) return;
        var id = SERVER_DRAFT_ID;
        SERVER_DRAFT_ID = null;
        fetch("/api/drafts/discard", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ draft_id: id })
        }).catch(function () { /* ignore */ });
    }

    function loadLocalDraft() {
        try {
            var raw = localStorage.getItem(DRAFT_KEY);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (e) {
            return null;
        }
    }

    function clearLocalDraft() {
        try { localStorage.removeItem(DRAFT_KEY); } catch (e) { /* ignore */ }
        clearServerDraft();
    }

    function restoreDraft(data) {
        if (data.candidate_name) candidateNameInput.value = data.candidate_name;
        if (data.mobile) mobileInput.value = data.mobile;
        if (data.salary) salaryInput.value = data.salary;
        if (data.aadhaar_filename) {
            selectedFileName = data.aadhaar_filename;
            fileName.textContent = data.aadhaar_filename;
            fileDisplay.style.display = "flex";
            uploadZone.style.display = "none";
        }
        if (data.cost_code) {
            ccDropdown.setValue(data.cost_code);
            onCostCodeChange(data.cost_code);
            if (data.role) {
                setTimeout(function () { roleDropdown.setValue(data.role); }, 100);
            }
            if (data.facility_type && !COST_CODE_FT[data.cost_code]) {
                setTimeout(function () { ftDropdown.setValue(data.facility_type); }, 100);
            }
            if (data.facility) {
                setTimeout(function () {
                    hubDropdown.setValue(data.facility);
                    var loc = LOCATION_BY_FACILITY[data.facility] || "";
                    $("locationDisplay").textContent = loc || "\u2014";
                    $("locationValue").value = loc;
                }, 100);
            }
        }
        /* trigger normalization displays */
        mobileInput.dispatchEvent(new Event("blur"));
        salaryInput.dispatchEvent(new Event("blur"));
        updateBatchProgress();
    }

    /* ── Event: Review Button ────────────────────────────────────────────── */
    btnReview.addEventListener("click", function () {
        if (!validateForm()) {
            showToast("Please fix the highlighted errors.", "error");
            return;
        }
        /* Check duplicate against the whole database */
        var mobile = normalizeMobile(mobileInput.value.trim());
        checkDuplicate(mobile, editCandidateId).then(function (res) {
            if (res.duplicate) {
                showDuplicateModal(res.existing);
            } else {
                showReview();
                clearLocalDraft();
            }
        });
    });

    /* ── Event: Back to Edit ─────────────────────────────────────────────── */
    btnBackToEdit.addEventListener("click", function () {
        hideReview();
    });

    /* ── Event: Save Draft ───────────────────────────────────────────────── */
    btnSaveDraft.addEventListener("click", function () {
        saveDraft().then(function (res) {
            if (res.errors) {
                var msgs = Object.values(res.errors).join("; ");
                showToast(msgs, "error");
                return;
            }
            if (isEditMode) {
                showToast("Candidate " + candidateNum + " updated!", "success");
            } else {
                showToast("Draft saved (ID: " + res.id + ")", "success");
            }
            /* Update candidate in navigation */
            var data = snapshotFormData();
            if (currentIndex >= 0) {
                candidates[currentIndex].data = data;
                candidates[currentIndex].status = "draft";
                candidates[currentIndex].id = res.id;
            } else {
                candidates.push({
                    id: res.id,
                    number: candidateNum,
                    status: "draft",
                    data: data
                });
                currentIndex = candidates.length - 1;
            }
            clearLocalDraft();
            updateNavBar();
            isEditMode = false;
            editCandidateId = null;
            updatePageTitle();
        });
    });

    /* ── Event: Confirm & Add Next ───────────────────────────────────────── */
    btnConfirmAddNext.addEventListener("click", function () {
        confirmCandidate().then(function (res) {
            if (res.errors) {
                var msgs = Object.values(res.errors).join("; ");
                showToast(msgs, "error");
                hideReview();
                return;
            }

            /* Update or add candidate */
            var data = snapshotFormData();
            if (currentIndex >= 0) {
                candidates[currentIndex].data = data;
                candidates[currentIndex].status = "ready";
                candidates[currentIndex].id = res.id;
            } else {
                candidates.push({
                    id: res.id,
                    number: candidateNum,
                    status: "ready",
                    data: data
                });
                currentIndex = candidates.length - 1;
            }

            batchSaved++;
            var prevNum = candidateNum;
            candidateNum++;
            showToast("Candidate " + prevNum + " confirmed!", "success");
            isEditMode = false;
            editCandidateId = null;

            /* Add next empty candidate */
            candidates.push({
                id: null,
                number: candidateNum,
                status: "draft",
                data: {}
            });
            currentIndex = candidates.length - 1;

            resetForm();
            updateNavBar();
            updatePageTitle();
            window.scrollTo({ top: 0, behavior: "smooth" });
        });
    });

    /* ── Event: Confirm & Finish ─────────────────────────────────────────── */
    btnConfirmFinish.addEventListener("click", function () {
        confirmCandidate().then(function (res) {
            if (res.errors) {
                var msgs = Object.values(res.errors).join("; ");
                showToast(msgs, "error");
                hideReview();
                return;
            }

            /* Update or add candidate */
            var data = snapshotFormData();
            if (currentIndex >= 0) {
                candidates[currentIndex].data = data;
                candidates[currentIndex].status = "ready";
                candidates[currentIndex].id = res.id;
            } else {
                candidates.push({
                    id: res.id,
                    number: candidateNum,
                    status: "ready",
                    data: data
                });
                currentIndex = candidates.length - 1;
            }

            batchSaved++;
            showToast("Batch complete! Redirecting to review...", "success");
            clearLocalDraft();
            setTimeout(function () {
                window.location.href = "/batch-review?batch_id=" + batchId;
            }, 800);
        });
    });

    /* ── Event: Batch Overview ───────────────────────────────────────────── */
    btnBatchOverview.addEventListener("click", function () {
        saveCurrentDraft();
        window.location.href = "/batch-review?batch_id=" + batchId;
    });

    /* ── Recovery Banner (local + server-side draft) ─────────────────────── */
    function hasMeaningfulDraft(d) {
        return d && (d.mobile || d.cost_code || d.facility || d.salary || d.candidate_name);
    }

    var serverDraft = null;
    function showRecoveryBanner() {
        if (isEditMode) return; /* do not distract while editing a real candidate */
        recoveryBanner.style.display = "flex";
    }

    fetch("/api/drafts?draft_type=manual_entry")
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data || !data.drafts || !data.drafts.length) return;
            var latest = data.drafts[0];
            if (!hasMeaningfulDraft(latest.safe_payload)) return;
            serverDraft = latest;
            SERVER_DRAFT_ID = latest.draft_id;
            var local = loadLocalDraft();
            var prefer = hasMeaningfulDraft(local) ? local : (latest.safe_payload || {});
            if (hasMeaningfulDraft(prefer) && !isEditMode) showRecoveryBanner();
        })
        .catch(function () { /* server unavailable; rely on local draft below */ });

    var draft = loadLocalDraft();
    if (hasMeaningfulDraft(draft)) showRecoveryBanner();

    $("btnRecover").addEventListener("click", function () {
        var toRestore = hasMeaningfulDraft(loadLocalDraft())
            ? loadLocalDraft()
            : (serverDraft ? serverDraft.safe_payload : null);
        if (toRestore) restoreDraft(toRestore);
        recoveryBanner.style.display = "none";
        showToast("Unsaved candidate recovered.", "info");
    });

    $("btnDiscard").addEventListener("click", function () {
        clearLocalDraft();
        if (serverDraft && serverDraft.draft_id) {
            fetch("/api/drafts/discard", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ draft_id: serverDraft.draft_id })
            }).catch(function () { /* ignore */ });
            serverDraft = null;
            SERVER_DRAFT_ID = null;
        }
        recoveryBanner.style.display = "none";
        showToast("Draft discarded.", "info");
    });

    /* ── Edit Mode ───────────────────────────────────────────────────────── */
    if (EDIT_CANDIDATE && EDIT_ID) {
        isEditMode = true;
        editCandidateId = parseInt(EDIT_ID, 10);
        candidateNum = EDIT_CANDIDATE.candidate_number || 1;
        batchSaved = Math.max(candidateNum - 1, 0);

        /* Add to navigation */
        candidates.push({
            id: editCandidateId,
            number: candidateNum,
            status: EDIT_CANDIDATE.status,
            data: {
                candidate_name: EDIT_CANDIDATE.name || "",
                mobile: EDIT_CANDIDATE.mobile || "",
                cost_code: EDIT_CANDIDATE.cost_code || "",
                role: EDIT_CANDIDATE.designation || "",
                facility_type: EDIT_CANDIDATE.facility_type || "",
                facility: EDIT_CANDIDATE.facility_name || "",
                salary: EDIT_CANDIDATE.salary_display || String(EDIT_CANDIDATE.salary || ""),
                aadhaar_filename: EDIT_CANDIDATE.aadhaar_filename || "",
                candidateNum: candidateNum,
                batchId: batchId
            }
        });
        currentIndex = 0;

        /* Restore form */
        restoreFormFromData(candidates[0].data);
        updatePageTitle();
        updateNavBar();
    }

    /* ── Auto-save on change ─────────────────────────────────────────────── */
    var autoSaveTimer = null;
    function scheduleAutoSave() {
        if (autoSaveTimer) clearTimeout(autoSaveTimer);
        autoSaveTimer = setTimeout(function () {
            saveLocalDraft();
            saveServerDraft();
        }, 1000);
    }
    mobileInput.addEventListener("input", scheduleAutoSave);
    salaryInput.addEventListener("input", scheduleAutoSave);
    candidateNameInput.addEventListener("input", function () {
        scheduleAutoSave();
        if (candidateNameInput.classList.contains("input-error")) {
            candidateNameInput.classList.remove("input-error");
            candidateNameError.style.display = "none";
        }
    });
    hubSearch.addEventListener("input", scheduleAutoSave);

    /* ── Init ────────────────────────────────────────────────────────────── */
    updateNavBar();
    if (!isEditMode) {
        updatePageTitle();
    }

})();
