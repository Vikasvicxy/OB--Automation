// Test harness that executes the real inline <script> from smart_upload.html
// against a minimal DOM/navigator stub so the copy-ready block and the
// facility-helper functions can be regression-tested with Node (no browser).
const fs = require("fs");

const templatePath = process.argv[2];
let html = fs.readFileSync(templatePath, "utf8").replace(
  "{{ rules_json | safe }}",
  JSON.stringify({ cost_codes: {} })
);
const match = html.match(/<script[^>]*>([\s\S]*?)<\/script>/);
if (!match) {
  console.error("NO_SCRIPT_BLOCK");
  process.exit(2);
}
const src = match[1];

function makeEl(id) {
  const el = {
    id,
    value: "",
    _attrs: {},
    style: {},
    textContent: "",
    innerHTML: "",
    className: "",
    type: "",
    disabled: false,
    addEventListener() {},
    removeEventListener() {},
    setAttribute(k, v) { el._attrs[k] = String(v); },
    getAttribute(k) { return k in el._attrs ? el._attrs[k] : null; },
    querySelectorAll() { return []; },
    appendChild() {},
    insertBefore() {},
    scrollIntoView() {},
    removeChild() {},
  };
  return el;
}

const els = new Map();
global.document = {
  getElementById(id) {
    if (!els.has(id)) els.set(id, makeEl(id));
    return els.get(id);
  },
  createElement() { return makeEl("created"); },
  activeElement: makeEl("active"),
  body: makeEl("body"),
  execCommand() { return true; },
};
global.window = { isSecureContext: true, addEventListener() {}, removeEventListener() {} };
let clipboardText = null;
global.navigator = {
  clipboard: { writeText(t) { clipboardText = t; return Promise.resolve(); } },
};
global.fetch = () => Promise.resolve({ json: () => Promise.resolve({ drafts: [], hubs: [], locations: {} }) });
global.confirm = () => true;
global.alert = () => {};
global.URLSearchParams = URLSearchParams;
global.sessionStorage = {
  _d: {},
  getItem(k) { return k in global.sessionStorage._d ? global.sessionStorage._d[k] : null; },
  setItem(k, v) { global.sessionStorage._d[k] = String(v); },
  removeItem(k) { delete global.sessionStorage._d[k]; },
};
global.localStorage = global.sessionStorage;

const lastIife = src.lastIndexOf("})();");
const exportCode =
  "\n;globalThis.__copyTest = {copyCurrentValues, copyFieldValue, formatDobForCopy, refreshCopyBlock};";
const script = lastIife === -1
  ? src + exportCode
  : src.slice(0, lastIife) + exportCode + src.slice(lastIife);

try {
  const fn = new Function("document", "navigator", "window", "fetch",
    "URLSearchParams", "setTimeout", "clearTimeout", script);
  fn(global.document, global.navigator, global.window, global.fetch,
     URLSearchParams, setTimeout, clearTimeout);
} catch (e) {
  console.error("HARNESS_BOOT_FAIL: " + (e && e.stack || e));
  process.exit(3);
}

function output() {
  const db = JSON.parse(process.argv[3] || "{}");
  const results = {};
  const fmtDob = global.__copyTest.formatDobForCopy;
  results.dob_iso = fmtDob("2006-05-06");
  results.dob_dmy = fmtDob("06/05/2006");
  results.dob_fill = fmtDob("6-5-2006");
  if (db.fields) {
    for (const k of Object.keys(db.fields)) {
      document.getElementById(k).value = db.fields[k];
    }
    global.__copyTest.copyCurrentValues(false);
    // wait a tick so the clipboard promise resolves
    setTimeout(() => {
      results.clip_plain = clipboardText;
      global.__copyTest.copyCurrentValues(true);
      setTimeout(() => {
        results.clip_headers = clipboardText;
        console.log(JSON.stringify(results));
        process.exit(0);
      }, 5);
    }, 5);
    return;
  }
  console.log(JSON.stringify(results));
  process.exit(0);
}
output();