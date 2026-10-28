// Lets Rem drive Raticode's own browser tabs in the background. Each tab gets
// its own DevTools session, so no debugging port is opened. Input is dispatched
// as page events because native input would take the user's keyboard focus.
//
// Access is granted per Rem turn. The backend asks for a capability bound to
// one thread's owner ID while that thread's Browser switch is on, and revokes
// it when the turn ends. The capability, not the request body, decides which
// tabs a caller can reach.
const http = require("node:http");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { hostOf, remNeedsConfirmation, remSiteAllowed } = require("./browser-policy.cjs");

// Written by earlier versions with a long-lived master token. Never written now.
const LEGACY_CONNECTION_FILE = "browser-control.json";
const AGENT_PATTERN = /^[A-Za-z0-9_-]{8,128}$/;
// Electron reserves world 999 for preload scripts.
const AGENT_WORLD_ID = 1917;
// Hidden tabs have no layout size. Give them a desktop viewport so pages
// render as they would on screen.
const FALLBACK_VIEWPORT = { width: 1280, height: 800 };
const MIN_VISIBLE_VIEWPORT = { width: 200, height: 150 };
const MAX_REQUEST_BYTES = 256 * 1024;
const MAX_RESULT_CHARS = 60000;
const NAVIGATION_TIMEOUT_MS = 30000;
const SETTLE_TIMEOUT_MS = 15000;
const MAX_AGENT_TABS = 8;
const MAX_GRANT_MS = 12 * 60 * 60 * 1000;
const DEFAULT_GRANT_MS = 4 * 60 * 60 * 1000;
// Idle Rem tabs stop burning CPU, then close.
const THROTTLE_IDLE_MS = 5 * 60 * 1000;
const CLOSE_IDLE_MS = 60 * 60 * 1000;
const MAX_UPLOAD_FILES = 20;
const MAX_UPLOAD_BYTES = 512 * 1024 * 1024;
const CONFIRM_TIMEOUT_MS = 2 * 60 * 1000;
const HIDDEN_SCREENSHOT_MESSAGE = "This tab is not on screen, so it has no image to capture. "
  + "Use browser_snapshot to read the page, or ask the user to show the Raticode editor area.";

const KEY_CODES = {
  Backspace: 8, Tab: 9, Enter: 13, Escape: 27, Space: 32, PageUp: 33, PageDown: 34, End: 35,
  Home: 36, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Insert: 45, Delete: 46,
  F1: 112, F2: 113, F3: 114, F4: 115, F5: 116, F6: 117, F7: 118, F8: 119, F9: 120, F10: 121,
  F11: 122, F12: 123,
};
const KEY_TEXT = { Enter: "\r", Space: " " };
const MODIFIERS = { Alt: "altKey", Control: "ctrlKey", Ctrl: "ctrlKey", Meta: "metaKey", Command: "metaKey", Shift: "shiftKey" };

// Runs in an isolated world: it shares the page DOM but none of its scripts.
// Element refs survive until the document navigates. Same-origin frames are
// walked too; their elements get refs like any other.
const PAGE_AGENT = String.raw`
globalThis.__raticodeAgent ||= (() => {
  const refs = new Map();
  const refOf = new WeakMap();
  let nextRef = 1;
  const MAX_LINES = 3000;
  const INPUT_ROLES = { button: "button", submit: "button", reset: "button", image: "button",
    checkbox: "checkbox", radio: "radio", range: "slider", number: "spinbutton",
    search: "searchbox", file: "button", color: "button" };
  const TAG_ROLES = { button: "button", textarea: "textbox", option: "option", nav: "navigation",
    main: "main", form: "form", dialog: "dialog", table: "table", ul: "list", ol: "list",
    li: "listitem", summary: "button", h1: "heading", h2: "heading", h3: "heading",
    h4: "heading", h5: "heading", h6: "heading", iframe: "iframe", tr: "row", th: "columnheader",
    td: "cell" };
  const INTERACTIVE = new Set(["button", "link", "textbox", "searchbox", "checkbox", "radio",
    "combobox", "listbox", "option", "slider", "spinbutton", "switch", "tab", "menuitem",
    "menuitemcheckbox", "menuitemradio", "treeitem", "clickable"]);
  const STRUCTURE = new Set(["heading", "navigation", "main", "form", "dialog", "alertdialog",
    "alert", "table", "row", "list", "listitem", "img", "iframe", "tablist", "menu", "tree",
    "columnheader", "cell"]);
  const NAME_FROM_CONTENT = new Set(["button", "link", "heading", "tab", "option", "menuitem",
    "menuitemcheckbox", "menuitemradio", "treeitem", "clickable", "columnheader", "cell"]);
  const SKIP = new Set(["script", "style", "noscript", "template", "head", "meta", "link"]);
  const SIGNIFICANT = "a[href],button,input:not([type=hidden]),select,textarea,[role],[tabindex],"
    + "[contenteditable=''],[contenteditable=true],h1,h2,h3,h4,h5,h6,img[alt],summary,iframe,"
    + "[onclick],table,ul,ol,form,nav,main,dialog";
  // Labels on buttons that commit something the user would want to approve.
  const CONSEQUENTIAL = /^(send|submit|buy|pay|place order|order|delete|remove|confirm|publish|post|transfer|purchase|checkout|check out|sign|approve|merge|deploy|save|update|reply|comment|share|invite|unsubscribe|close account|book|reserve|subscribe)\b/i;

  const clean = (value, limit = 200) => {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
  };
  const frameDocument = (frame) => {
    try { return frame.contentDocument && frame.contentDocument.body ? frame.contentDocument : null; }
    catch { return null; }
  };
  const allDocuments = () => {
    const docs = [document];
    for (let index = 0; index < docs.length && docs.length < 50; index += 1) {
      for (const frame of docs[index].querySelectorAll("iframe,frame")) {
        const doc = frameDocument(frame);
        if (doc) docs.push(doc);
      }
    }
    return docs;
  };
  function refFor(element) {
    let ref = refOf.get(element);
    if (!ref) {
      ref = "e" + nextRef++;
      refOf.set(element, ref);
      refs.set(ref, new WeakRef(element));
    }
    return ref;
  }
  function target(arg) {
    if (arg.selector) {
      for (const doc of allDocuments()) {
        const found = doc.querySelector(arg.selector);
        if (found) return found;
      }
      throw new Error("No element matches " + arg.selector + ".");
    }
    const element = refs.get(String(arg.ref || ""))?.deref();
    if (!element || !element.isConnected) {
      throw new Error("Element " + (arg.ref || "(missing ref)") + " is gone. Take a new snapshot.");
    }
    return element;
  }
  const windowOf = (element) => element.ownerDocument?.defaultView || window;
  function roleOf(element) {
    const explicit = (element.getAttribute("role") || "").trim().split(/\s+/)[0];
    if (explicit && explicit !== "presentation" && explicit !== "none") return explicit;
    const tag = element.localName;
    if (tag === "a" || tag === "area") return element.hasAttribute("href") ? "link" : "";
    if (tag === "input") return element.type === "hidden" ? "" : INPUT_ROLES[element.type] || "textbox";
    if (tag === "select") return element.multiple || element.size > 1 ? "listbox" : "combobox";
    if (tag === "img") return element.getAttribute("alt") ? "img" : "";
    if (tag === "frame") return "iframe";
    if (TAG_ROLES[tag]) return TAG_ROLES[tag];
    if (element.isContentEditable && !element.parentElement?.isContentEditable) return "textbox";
    if (element.hasAttribute("onclick") || element.tabIndex >= 0 && element.hasAttribute("tabindex")) return "clickable";
    return "";
  }
  function labelText(element) {
    const ids = (element.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    if (ids.length) return ids.map((id) => element.ownerDocument.getElementById(id)?.textContent || "").join(" ");
    return element.getAttribute("aria-label") || "";
  }
  function nameOf(element, role) {
    const label = clean(labelText(element));
    if (label) return { name: label, fromContent: false };
    if (["input", "textarea", "select"].includes(element.localName)) {
      const labelled = element.labels?.[0]?.textContent || element.getAttribute("placeholder")
        || element.getAttribute("title") || (["submit", "button", "reset"].includes(element.type) ? element.value : "");
      return { name: clean(labelled), fromContent: false };
    }
    if (element.localName === "img") return { name: clean(element.getAttribute("alt")), fromContent: false };
    if (role === "iframe") return { name: clean(element.getAttribute("title") || element.getAttribute("name")), fromContent: false };
    if (NAME_FROM_CONTENT.has(role)) return { name: clean(element.innerText || element.textContent, 120), fromContent: true };
    return { name: clean(element.getAttribute("title")), fromContent: false };
  }
  function visible(element) {
    if (element.getAttribute("aria-hidden") === "true") return false;
    if (typeof element.checkVisibility === "function") {
      return element.checkVisibility({ visibilityProperty: true });
    }
    const style = windowOf(element).getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden";
  }
  function attributes(element, role) {
    const parts = [];
    if (role === "heading") parts.push("[level=" + (Number(element.localName.slice(1)) || element.getAttribute("aria-level") || 2) + "]");
    if (element.checked === true || element.getAttribute("aria-checked") === "true") parts.push("[checked]");
    if (element.disabled === true || element.getAttribute("aria-disabled") === "true") parts.push("[disabled]");
    const expanded = element.getAttribute("aria-expanded");
    if (expanded) parts.push("[expanded=" + expanded + "]");
    if (element.getAttribute("aria-selected") === "true" || element.selected === true) parts.push("[selected]");
    if (element === element.ownerDocument.activeElement) parts.push("[focused]");
    if (["textbox", "searchbox", "combobox", "spinbutton", "slider"].includes(role)) {
      const value = element.isContentEditable ? element.innerText : element.value;
      if (value) parts.push("value=" + JSON.stringify(element.type === "password" ? "••••" : clean(value, 300)));
    }
    if (element.localName === "input" && element.type === "file") parts.push("[file-upload]");
    if (role === "link") parts.push("[url=" + clean(element.href, 300) + "]");
    return parts;
  }
  function snapshot() {
    const lines = [];
    let truncated = false;
    const push = (depth, text) => {
      if (lines.length >= MAX_LINES) { truncated = true; return false; }
      lines.push("  ".repeat(depth) + "- " + text);
      return true;
    };
    const walk = (node, depth, quiet) => {
      if (truncated) return;
      if (node.nodeType === Node.TEXT_NODE) {
        const text = clean(node.textContent, 300);
        if (text && !quiet) push(depth, "text: " + JSON.stringify(text));
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
      let childDepth = depth;
      let childQuiet = quiet;
      if (node.nodeType === Node.ELEMENT_NODE) {
        const element = node;
        if (SKIP.has(element.localName) || !visible(element)) return;
        const role = roleOf(element);
        const interactive = INTERACTIVE.has(role);
        if (interactive || STRUCTURE.has(role)) {
          const { name, fromContent } = nameOf(element, role);
          const parts = [role];
          if (name) parts.push(JSON.stringify(name));
          parts.push(...attributes(element, role));
          if (interactive) parts.push("[ref=" + refFor(element) + "]");
          const frameDoc = role === "iframe" ? frameDocument(element) : null;
          if (role === "iframe" && !frameDoc) {
            parts.push("(cross-origin frame" + (element.src ? " " + clean(element.src, 200) : "") + "; not inspected. Open its URL in a tab to read it)");
          }
          if (!push(depth, parts.join(" "))) return;
          if (frameDoc) { walk(frameDoc.body, depth + 1, false); return; }
          if (role === "iframe" || element.localName === "select") return;
          childDepth = depth + 1;
          childQuiet = quiet || fromContent;
        } else if (!quiet && !element.shadowRoot && !element.querySelector(SIGNIFICANT)) {
          // Merge plain text blocks into one line instead of one per inline element.
          const text = clean(element.innerText, 500);
          if (text) push(depth, "text: " + JSON.stringify(text));
          return;
        }
        if (element.shadowRoot) walk(element.shadowRoot, childDepth, childQuiet);
      }
      for (const child of node.childNodes) walk(child, childDepth, childQuiet);
    };
    if (document.body) walk(document.body, 0, false);
    return {
      lines: lines.join("\n"),
      truncated,
      scroll: { x: Math.round(scrollX), y: Math.round(scrollY),
        height: Math.round(document.documentElement?.scrollHeight || 0) },
      viewport: { width: innerWidth, height: innerHeight },
    };
  }
  const FOCUSABLE = "a[href],area[href],button,input:not([type=hidden]),select,textarea,summary,"
    + "iframe,[tabindex],[contenteditable=''],[contenteditable=true]";
  // Offset of a same-origin frame's viewport inside the top-level viewport.
  function frameOffset(element) {
    let x = 0;
    let y = 0;
    let win = windowOf(element);
    while (win !== window && win.frameElement) {
      const frame = win.frameElement;
      const rect = frame.getBoundingClientRect();
      x += rect.left + frame.clientLeft;
      y += rect.top + frame.clientTop;
      win = win.parent;
    }
    return { x, y };
  }
  function box(element) {
    const rect = element.getBoundingClientRect();
    const offset = frameOffset(element);
    return { x: Math.round(rect.left + offset.x), y: Math.round(rect.top + offset.y),
      width: Math.round(rect.width), height: Math.round(rect.height) };
  }
  function locate(arg) {
    const element = target(arg);
    element.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) throw new Error("That element has no visible area.");
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const top = element.ownerDocument.elementFromPoint(x, y);
    const inside = top && (top === element || element.contains(top));
    const covered = top && !inside && !top.contains(element)
      ? clean(top.localName + (top.id ? "#" + top.id : "") + " " + (top.innerText || ""), 80)
      : "";
    return { element, receiver: inside ? top : element, x, y, covered, box: box(element) };
  }
  function mouse(view, x, y, button, buttons, detail) {
    return { bubbles: true, cancelable: true, composed: true, view, clientX: x, clientY: y,
      screenX: screenX + x, screenY: screenY + y, button, buttons, detail,
      pointerId: 1, pointerType: "mouse", isPrimary: true };
  }
  function focusable(node) {
    for (let element = node; element; element = element.parentElement || element.getRootNode?.().host) {
      if (element.matches?.(FOCUSABLE) && !element.disabled) return element;
    }
    return null;
  }
  // Events are dispatched inside the page. Native input would move keyboard
  // focus into this tab and away from whatever the user is doing.
  function enterPointer(receiver, x, y) {
    const init = mouse(windowOf(receiver), x, y, 0, 0, 0);
    for (const [Type, type] of [[PointerEvent, "pointerover"], [MouseEvent, "mouseover"],
      [PointerEvent, "pointerenter"], [MouseEvent, "mouseenter"], [PointerEvent, "pointermove"],
      [MouseEvent, "mousemove"]]) {
      receiver.dispatchEvent(new Type(type, { ...init, bubbles: !type.endsWith("enter") }));
    }
  }
  function hover(arg) {
    const { receiver, x, y, covered, box: area } = locate(arg);
    enterPointer(receiver, x, y);
    return { covered, box: area };
  }
  function submitButtonFor(element) {
    const button = element.closest?.("button,input[type=submit],input[type=image]");
    if (!button) return null;
    if (button.localName === "button" && !["", "submit"].includes((button.getAttribute("type") || "").toLowerCase())) return null;
    return button.form ? button : null;
  }
  // What a click or Enter would do, so risky actions can be confirmed first.
  function intent(arg) {
    const element = arg.focused ? deepActive() : target(arg);
    const doc = element.ownerDocument;
    if (arg.focused) {
      const form = element.localName === "input" ? element.form : null;
      return { consequential: Boolean(form), label: form ? "submit the form" : "press Enter",
        url: doc.location.href, box: box(element) };
    }
    const actionable = element.closest?.("button,a[href],input,[role=button],[role=link],[role=menuitem],summary") || element;
    const label = clean(labelText(actionable) || actionable.innerText || actionable.value || actionable.getAttribute?.("title") || "", 80);
    const submits = Boolean(submitButtonFor(element));
    return { consequential: submits || CONSEQUENTIAL.test(label), label: label || actionable.localName,
      url: doc.location.href, box: box(element) };
  }
  function click(arg) {
    const { receiver, x, y, covered, box: area } = locate(arg);
    enterPointer(receiver, x, y);
    const view = windowOf(receiver);
    const button = { left: 0, middle: 1, right: 2 }[arg.button] ?? 0;
    const buttons = [1, 4, 2][button];
    const fire = (Type, type, init) => receiver.dispatchEvent(new Type(type, init));
    const clicks = arg.clicks === 2 ? 2 : 1;
    for (let detail = 1; detail <= clicks; detail += 1) {
      const down = mouse(view, x, y, button, buttons, detail);
      const up = mouse(view, x, y, button, 0, detail);
      fire(PointerEvent, "pointerdown", down);
      if (fire(MouseEvent, "mousedown", down) && detail === 1) {
        const next = focusable(receiver);
        const active = receiver.ownerDocument.activeElement;
        if (next) next.focus({ preventScroll: true });
        else if (active && active !== receiver.ownerDocument.body) active.blur();
      }
      fire(PointerEvent, "pointerup", up);
      fire(MouseEvent, "mouseup", up);
      if (button === 0) fire(PointerEvent, "click", up);
      else fire(PointerEvent, "auxclick", up);
      if (button === 2) fire(PointerEvent, "contextmenu", up);
    }
    if (clicks === 2 && button === 0) fire(MouseEvent, "dblclick", mouse(view, x, y, 0, 0, 2));
    return { covered, box: area };
  }
  function editableField(element) {
    return element?.isContentEditable
      || element?.localName === "textarea"
      || element?.localName === "input" && !["button", "checkbox", "color", "file", "image", "radio",
        "range", "reset", "submit"].includes(element.type);
  }
  function setValue(element, value) {
    const view = windowOf(element);
    const prototype = element.localName === "textarea" ? view.HTMLTextAreaElement.prototype : view.HTMLInputElement.prototype;
    // The native setter reaches frameworks that wrap the value property.
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
  }
  function type(arg) {
    const element = target(arg);
    const doc = element.ownerDocument;
    element.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    if (!editableField(element) || element.disabled || element.readOnly) {
      throw new Error("That element does not accept typed text.");
    }
    element.focus({ preventScroll: true });
    const text = String(arg.text ?? "");
    const before = element.isContentEditable ? element.innerText : element.value;
    if (element.isContentEditable) {
      const range = doc.createRange();
      range.selectNodeContents(element);
      if (arg.clear === false) range.collapse(false);
      doc.getSelection().removeAllRanges();
      doc.getSelection().addRange(range);
    } else if (arg.clear !== false) {
      element.select();
    } else {
      try { element.setSelectionRange(element.value.length, element.value.length); } catch { /* Some input types have no caret. */ }
    }
    const inserted = text
      ? doc.execCommand("insertText", false, text)
      : arg.clear === false || doc.execCommand("delete");
    if (!inserted && !element.isContentEditable) {
      setValue(element, arg.clear === false ? before + text : text);
      element.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: text }));
    }
    if (!element.isContentEditable) element.dispatchEvent(new Event("change", { bubbles: true }));
    const value = element.isContentEditable ? element.innerText : element.value;
    return { value: element.type === "password" ? "••••" : clean(value, 300), box: box(element) };
  }
  function deepActive() {
    let element = document.activeElement || document.body;
    for (let depth = 0; depth < 20; depth += 1) {
      if (element?.shadowRoot?.activeElement) { element = element.shadowRoot.activeElement; continue; }
      const frameDoc = element && ["iframe", "frame"].includes(element.localName) ? frameDocument(element) : null;
      if (frameDoc?.activeElement) { element = frameDoc.activeElement; continue; }
      break;
    }
    return element || document.body;
  }
  function tabbable(doc) {
    return [...doc.querySelectorAll(FOCUSABLE)]
      .filter((element) => element.tabIndex >= 0 && !element.disabled && visible(element))
      .sort((a, b) => (a.tabIndex || Infinity) - (b.tabIndex || Infinity));
  }
  function moveFocus(from, direction) {
    const order = tabbable(from.ownerDocument || document);
    if (!order.length) return;
    const index = order.indexOf(from);
    const next = order[(index + direction + order.length + (index < 0 && direction < 0 ? 1 : 0)) % order.length];
    next.focus();
  }
  function enter(element) {
    const doc = element.ownerDocument;
    if (element.localName === "textarea") doc.execCommand("insertText", false, "\n");
    else if (element.isContentEditable) doc.execCommand("insertParagraph");
    else if (element.localName === "input" && element.form) {
      // Implicit submission uses the form's default button when there is one.
      const button = element.form.querySelector("button:not([type]),button[type=submit],input[type=submit],input[type=image]");
      if (button) { if (!button.disabled) button.click(); }
      else element.form.requestSubmit();
    } else if (element.matches?.("a[href],button,summary,[role=button],[role=link],[role=menuitem],[role=tab],input[type=button],input[type=submit],input[type=reset]")) {
      element.click();
    }
  }
  function press(arg) {
    const element = deepActive();
    const doc = element.ownerDocument;
    const init = { key: arg.key, code: arg.code, keyCode: arg.keyCode, which: arg.keyCode,
      ctrlKey: arg.ctrlKey, altKey: arg.altKey, shiftKey: arg.shiftKey, metaKey: arg.metaKey,
      bubbles: true, cancelable: true, composed: true, view: windowOf(element) };
    const key = (type, extra = {}) => element.dispatchEvent(new KeyboardEvent(type, { ...init, ...extra }));
    if (key("keydown")) {
      const shortcut = arg.ctrlKey || arg.metaKey;
      if (arg.text && !shortcut) {
        const charCode = arg.text.charCodeAt(0);
        if (key("keypress", { charCode, keyCode: charCode, which: charCode })) {
          if (arg.key === "Enter") enter(element);
          else if (editableField(element)) doc.execCommand("insertText", false, arg.text);
          else if (arg.key === " " && element.matches?.("button,summary,input[type=checkbox],input[type=radio],input[type=button],input[type=submit],[role=button],[role=checkbox]")) element.click();
        }
      } else if (arg.key === "Tab" && !shortcut) {
        moveFocus(element, arg.shiftKey ? -1 : 1);
      } else if (arg.key === "Backspace" && editableField(element)) {
        doc.execCommand("delete");
      } else if (arg.key === "Delete" && editableField(element)) {
        doc.execCommand("forwardDelete");
      } else if (shortcut && arg.key.toLowerCase() === "a") {
        if (element.select) element.select(); else doc.execCommand("selectAll");
      } else if (element.localName === "select" && ["ArrowDown", "ArrowUp"].includes(arg.key)) {
        const index = element.selectedIndex + (arg.key === "ArrowDown" ? 1 : -1);
        if (index >= 0 && index < element.options.length) {
          element.selectedIndex = index;
          element.dispatchEvent(new Event("input", { bubbles: true }));
          element.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
    }
    key("keyup");
    const focused = deepActive();
    return { focused: focused === focused.ownerDocument.body ? "page" : clean(focused.localName + (focused.id ? "#" + focused.id : ""), 80) };
  }
  function select(arg) {
    const element = target(arg);
    if (element.localName !== "select") throw new Error("That element is not a select list.");
    const wanted = new Set((arg.values || []).map(String));
    const matches = [...element.options].filter((option) => (
      wanted.has(option.value) || wanted.has(clean(option.label || option.text))
    ));
    const chosen = element.multiple ? matches : matches.slice(0, 1);
    if (element.multiple) {
      for (const option of element.options) option.selected = chosen.includes(option);
    } else if (chosen.length) {
      element.value = chosen[0].value;
    }
    if (!chosen.length) throw new Error("No option matches " + [...wanted].join(", ") + ".");
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return { selected: chosen.map((option) => clean(option.label || option.text)), box: box(element) };
  }
  function hasText(arg) {
    const wanted = String(arg.text || "");
    return allDocuments().some((doc) => (doc.body?.innerText || "").includes(wanted));
  }
  // Tags a file input so the DevTools DOM domain can find it for an upload.
  function markUpload(arg) {
    const element = target(arg);
    if (element.localName !== "input" || element.type !== "file") {
      throw new Error("That element is not a file input. Use a ref marked [file-upload] in the snapshot.");
    }
    if (element.disabled) throw new Error("That file input is disabled.");
    element.setAttribute("data-raticode-upload", String(arg.nonce));
    return { multiple: element.multiple, box: box(element) };
  }
  function unmarkUpload(arg) {
    for (const doc of allDocuments()) {
      for (const element of doc.querySelectorAll("[data-raticode-upload]")) {
        if (element.getAttribute("data-raticode-upload") === String(arg.nonce)) element.removeAttribute("data-raticode-upload");
      }
    }
    return true;
  }
  const methods = { snapshot, click, hover, type, press, select, hasText, intent, markUpload, unmarkUpload };
  return (method, arg) => {
    try { return { value: methods[method](arg || {}) }; }
    catch (error) { return { error: String(error?.message || error) }; }
  };
})();
`;

// Capabilities are random tokens kept only in memory, looked up by hash so
// comparisons never leak timing about stored values.
function createCapabilityRegistry({ now = () => Date.now() } = {}) {
  const grants = new Map();
  const digest = (token) => crypto.createHash("sha256").update(String(token ?? "")).digest("hex");

  function grant({ owner, label = "Rem", ttlMs = DEFAULT_GRANT_MS, uploadRoots = [] } = {}) {
    if (!AGENT_PATTERN.test(String(owner ?? ""))) throw new Error("Missing browser agent identity.");
    const token = crypto.randomBytes(32).toString("hex");
    const lifetime = Math.min(Math.max(Number(ttlMs) || DEFAULT_GRANT_MS, 1000), MAX_GRANT_MS);
    grants.set(digest(token), {
      owner,
      label: typeof label === "string" && label.trim() ? label.trim().slice(0, 60) : "Rem",
      uploadRoots: Array.isArray(uploadRoots)
        ? uploadRoots.filter((item) => typeof item === "string" && path.isAbsolute(item)).slice(0, 50)
        : [],
      expiresAt: now() + lifetime,
    });
    return { capability: token, expiresAt: now() + lifetime };
  }

  function resolve(token) {
    if (!/^[0-9a-f]{64}$/.test(String(token ?? ""))) return null;
    const key = digest(token);
    const found = grants.get(key);
    if (!found) return null;
    if (found.expiresAt <= now()) {
      grants.delete(key);
      return null;
    }
    return found;
  }

  function revoke(token) {
    return grants.delete(digest(token));
  }

  function revokeOwner(owner) {
    for (const [key, value] of grants) if (value.owner === owner) grants.delete(key);
  }

  function revokeAll() {
    grants.clear();
  }

  function activeOwners() {
    const time = now();
    return new Set([...grants.values()].filter((item) => item.expiresAt > time).map((item) => item.owner));
  }

  return { activeOwners, grant, resolve, revoke, revokeAll, revokeOwner };
}

function createAgentBrowserController({
  openTab,
  agentTabs,
  allAgentTabs = () => [],
  closeTab,
  normalizeUrl,
  policy = () => ({}),
  confirm = async () => false,
  record = () => {},
  now = () => Date.now(),
}) {
  async function handle(request) {
    if (!request || typeof request !== "object") throw new Error("Send a JSON object.");
    const agent = String(request.agent || "");
    if (!AGENT_PATTERN.test(agent)) throw new Error("Missing browser agent identity.");
    const label = typeof request.label === "string" && request.label.trim()
      ? request.label.trim().slice(0, 60)
      : "Rem";
    const action = String(request.action || "");
    if (action === "tabs") return { tabs: agentTabs(agent).map(tabSummary) };
    if (action === "open") {
      const url = request.url ? agentUrl(request.url, normalizeUrl, policy()) : "";
      const owned = agentTabs(agent).filter((item) => !item.agent?.shared);
      if (owned.length >= MAX_AGENT_TABS) {
        throw new Error(`Rem already has ${MAX_AGENT_TABS} tabs open for this task. Close one with browser_close first.`);
      }
      const session = openTab({ agent, label });
      session.agentUploadRoots = Array.isArray(request.uploadRoots) ? request.uploadRoots : [];
      try {
        await waitForContents(session);
        await prepareTab(session);
      } catch (error) {
        closeTab(session);
        throw error;
      }
      touch(session);
      if (url) {
        log(session, "open", url);
        await navigate(session, url);
      }
      return { tab: session.id, ...tabSummary(session) };
    }
    const session = findTab(agent, request.tab);
    session.agentUploadRoots = Array.isArray(request.uploadRoots) ? request.uploadRoots : [];
    return queue(session, async () => {
      if (action !== "close" && session.agentPaused) {
        throw new Error("The user paused Rem in this tab. Wait for the user to resume, or ask them what to do.");
      }
      if (action === "close") {
        log(session, "close", "");
        closeTab(session);
        return { closed: session.id };
      }
      if (action === "dialog") return handleDialog(session, request);
      await prepareTab(session);
      touch(session);
      switch (action) {
        case "navigate": {
          const url = agentUrl(request.url, normalizeUrl, policy());
          log(session, "navigate", url);
          await navigate(session, url);
          return tabSummary(session);
        }
        case "back":
        case "forward": {
          const history = contentsOf(session).navigationHistory;
          const can = action === "back" ? history.canGoBack() : history.canGoForward();
          if (!can) throw new Error(`There is no page to go ${action} to.`);
          log(session, action, "");
          if (action === "back") history.goBack(); else history.goForward();
          await settle(session);
          return tabSummary(session);
        }
        case "reload":
          log(session, "reload", "");
          contentsOf(session).reload();
          await settle(session);
          return tabSummary(session);
        case "snapshot":
          return snapshot(session);
        case "click":
          return click(session, request);
        case "hover": {
          const result = await inPage(session, "hover", targetArg(request));
          void highlight(session, result?.box);
          return withWarning(result, tabSummary(session));
        }
        case "type":
          return type(session, request);
        case "press":
          return press(session, request);
        case "select": {
          const result = await inPage(session, "select", {
            ...targetArg(request),
            values: Array.isArray(request.values) ? request.values.slice(0, 100) : [],
          });
          void highlight(session, result?.box);
          log(session, "select", (result?.selected || []).join(", "));
          return { selected: result?.selected || [], ...tabSummary(session) };
        }
        case "evaluate":
          return evaluate(session, request.expression);
        case "upload":
          return upload(session, request);
        case "screenshot":
          return screenshot(session);
        case "wait":
          return wait(session, request);
        default:
          throw new Error(`Unknown browser action: ${action || "missing"}.`);
      }
    });
  }

  function findTab(agent, tab) {
    const tabs = agentTabs(agent);
    if (tab) {
      const session = tabs.find((item) => item.id === tab);
      if (!session) throw new Error(`Tab ${tab} was closed or belongs to another task. Call browser_tabs.`);
      return session;
    }
    const latest = tabs.sort((a, b) => (b.agentUsedAt || 0) - (a.agentUsedAt || 0))[0];
    if (!latest) throw new Error("No browser tab is open for this task. Call browser_open first.");
    return latest;
  }

  function touch(session) {
    session.agentUsedAt = now();
  }

  function log(session, action, detail) {
    try { record(session, { action, detail: String(detail ?? "").slice(0, 300), at: now() }); } catch { /* Logging is best effort. */ }
  }

  // Risky actions on signed-in pages wait for the user's approval.
  async function guard(session, action, details) {
    const settings = policy();
    if (!remNeedsApproval(settings, liveContents(session)?.getURL() || "", session, details.consequential)) return;
    log(session, "approval", `${action}: ${details.label}`);
    const approved = await withTimeout(
      Promise.resolve(confirm(session, { action, label: details.label, url: details.url || liveContents(session)?.getURL() || "" })),
      CONFIRM_TIMEOUT_MS,
      "",
    );
    if (approved !== true) {
      log(session, "declined", `${action}: ${details.label}`);
      throw new Error(`The user did not approve "${details.label}". Do not retry it; ask the user how to proceed.`);
    }
  }

  async function click(session, request) {
    const target = targetArg(request);
    const intent = await inPage(session, "intent", target);
    await guard(session, "click", intent);
    void highlight(session, intent?.box);
    log(session, "click", intent?.label || "");
    const result = await inPage(session, "click", {
      ...target,
      button: ["left", "right", "middle"].includes(request.button) ? request.button : "left",
      clicks: request.doubleClick === true ? 2 : 1,
    });
    await settle(session);
    return withWarning(result, tabSummary(session));
  }

  async function type(session, request) {
    const text = String(request.text ?? "");
    if (text.length > 20000) throw new Error("Type at most 20000 characters at a time.");
    const target = targetArg(request);
    const { value, box } = await inPage(session, "type", { ...target, text, clear: request.clear !== false });
    void highlight(session, box);
    log(session, "type", request.submit === true ? "text and Enter" : "text");
    if (request.submit === true) await press(session, { key: "Enter" }, { quiet: true });
    await settle(session);
    return { ...tabSummary(session), value };
  }

  async function press(session, request, { quiet = false } = {}) {
    const key = parseKey(request.key);
    if (key.key === "Enter") await guard(session, "press Enter", await inPage(session, "intent", { focused: true }));
    if (!quiet) log(session, "press", request.key);
    const { focused } = await inPage(session, "press", key);
    await settle(session);
    return { ...tabSummary(session), focused };
  }

  async function evaluate(session, expression) {
    const settings = policy();
    if (settings.remEvaluate === false) {
      throw new Error("Running page scripts is turned off in Settings > Browser. Use snapshot, click and type instead.");
    }
    const source = String(expression ?? "").trim();
    if (!source) throw new Error("Pass a JavaScript expression.");
    if (source.length > 100000) throw new Error("Keep the expression under 100000 characters.");
    await guard(session, "run a page script", { consequential: true, label: "Run a script on this page" });
    log(session, "evaluate", source.slice(0, 120));
    const response = await withTimeout(cdp(session, "Runtime.evaluate", {
      expression: source,
      awaitPromise: true,
      returnByValue: true,
      userGesture: true,
    }), 30000, "The expression did not finish within 30 seconds.");
    if (response.exceptionDetails) {
      const details = response.exceptionDetails;
      throw new Error(details.exception?.description || details.text || "The expression threw an error.");
    }
    let value = response.result?.value;
    if (value === undefined && response.result?.type !== "undefined") value = response.result?.description;
    let text = value === undefined ? "undefined" : JSON.stringify(value);
    if (text && text.length > MAX_RESULT_CHARS) text = `${text.slice(0, MAX_RESULT_CHARS)}… (truncated)`;
    return { tab: session.id, result: text };
  }

  async function upload(session, request) {
    const files = uploadPaths(request.paths, session.agentUploadRoots || []);
    const nonce = crypto.randomBytes(8).toString("hex");
    const marked = await inPage(session, "markUpload", { ...targetArg(request), nonce });
    if (files.length > 1 && !marked.multiple) {
      await inPage(session, "unmarkUpload", { nonce });
      throw new Error("That file input accepts one file.");
    }
    try {
      await guard(session, "upload files", { consequential: true, label: `Upload ${files.map((file) => path.basename(file)).join(", ")}` });
      void highlight(session, marked.box);
      log(session, "upload", files.map((file) => path.basename(file)).join(", "));
      await cdp(session, "DOM.getDocument", { depth: 0 });
      const search = await cdp(session, "DOM.performSearch", { query: `[data-raticode-upload="${nonce}"]` });
      try {
        if (!search.resultCount) throw new Error("The file input disappeared. Take a new snapshot.");
        const { nodeIds } = await cdp(session, "DOM.getSearchResults", { searchId: search.searchId, fromIndex: 0, toIndex: 1 });
        await cdp(session, "DOM.setFileInputFiles", { files, nodeId: nodeIds[0] });
      } finally {
        await cdp(session, "DOM.discardSearchResults", { searchId: search.searchId }).catch(() => {});
      }
    } finally {
      await inPage(session, "unmarkUpload", { nonce }).catch(() => {});
    }
    await settle(session);
    return { ...tabSummary(session), uploaded: files.map((file) => path.basename(file)) };
  }

  async function handleDialog(session, request) {
    const dialog = session.agentDialog;
    if (!dialog) throw new Error("No dialog is open in this tab.");
    const accept = request.accept !== false;
    log(session, "dialog", `${accept ? "accepted" : "dismissed"} ${dialog.type}`);
    await cdp(session, "Page.handleJavaScriptDialog", {
      accept,
      ...(typeof request.promptText === "string" ? { promptText: request.promptText.slice(0, 10000) } : {}),
    });
    session.agentDialog = null;
    await settle(session);
    return tabSummary(session);
  }

  async function viewportChanged(session) {
    if (!session?.agent || !liveContents(session)) return;
    try { await applyViewport(session); } catch { /* The next agent action retries. */ }
  }

  // Called periodically: idle tabs are throttled, then closed.
  function sweep() {
    const time = now();
    for (const session of allAgentTabs()) {
      const contents = liveContents(session);
      if (!contents) continue;
      const idle = time - (session.agentUsedAt || 0);
      if (idle >= CLOSE_IDLE_MS && !session.agent?.shared) {
        closeTab(session);
        continue;
      }
      if (idle >= THROTTLE_IDLE_MS && !session.agentThrottled) {
        session.agentThrottled = true;
        try { contents.setBackgroundThrottling(true); } catch { /* The view closed. */ }
      }
    }
  }

  return { handle, sweep, viewportChanged };
}

function remNeedsApproval(settings, url, session, consequential) {
  return remNeedsConfirmation({ policy: settings, url, sharedByUser: Boolean(session.agent?.shared), consequential });
}

function agentUrl(value, normalizeUrl, settings = {}) {
  const input = String(value ?? "").trim();
  if (!input) throw new Error("Enter a URL.");
  if (input === "about:blank") return input;
  const url = new URL(normalizeUrl(input));
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("Rem can open http and https pages only.");
  }
  if (!remSiteAllowed(url.toString(), settings)) {
    const host = hostOf(url.toString());
    const allowed = settings.remAllowedSites || [];
    throw new Error(allowed.length && !(settings.remBlockedSites || []).some((site) => host === site || host.endsWith(`.${site}`))
      ? `Rem may only open ${allowed.join(", ")}. The user can change this in Settings > Browser.`
      : `The user blocked Rem from ${host} in Settings > Browser.`);
  }
  return url.toString();
}

// Only regular files inside the turn's project and trusted folders.
function uploadPaths(value, roots) {
  const requested = Array.isArray(value) ? value : [];
  if (!requested.length) throw new Error("Pass paths: the absolute paths of the files to upload.");
  if (requested.length > MAX_UPLOAD_FILES) throw new Error(`Upload at most ${MAX_UPLOAD_FILES} files at a time.`);
  const realRoots = roots.flatMap((root) => {
    try { return [fs.realpathSync.native(root)]; } catch { return []; }
  });
  if (!realRoots.length) throw new Error("This task has no project folder, so Rem cannot upload files.");
  return requested.map((item) => {
    if (typeof item !== "string" || !path.isAbsolute(item)) throw new Error("Upload paths must be absolute.");
    let real;
    try { real = fs.realpathSync.native(item); } catch { throw new Error(`File not found: ${item}`); }
    const inside = realRoots.some((root) => {
      const relative = path.relative(root, real);
      return relative && !relative.startsWith("..") && !path.isAbsolute(relative);
    });
    if (!inside) throw new Error(`${item} is outside this task's project folders.`);
    const stat = fs.statSync(real);
    if (!stat.isFile()) throw new Error(`${item} is not a file.`);
    if (stat.size > MAX_UPLOAD_BYTES) throw new Error(`${item} is larger than 512 MB.`);
    return real;
  });
}

function tabSummary(session) {
  const contents = liveContents(session);
  return {
    tab: session.id,
    url: contents?.getURL() || "about:blank",
    title: contents?.getTitle() || "",
    loading: contents?.isLoading() === true,
    ...(session.agent?.shared ? { sharedByUser: true } : {}),
    ...(session.agentDialog ? { dialog: session.agentDialog } : {}),
    ...(session.lastBlockedDownload ? { blockedDownload: session.lastBlockedDownload } : {}),
    ...(session.error ? { error: session.error } : {}),
  };
}

function liveContents(session) {
  const contents = session?.contents;
  return contents && !contents.isDestroyed() ? contents : null;
}

function contentsOf(session) {
  const contents = liveContents(session);
  if (!contents) throw new Error(`Tab ${session.id} was closed. Call browser_open for a new tab.`);
  return contents;
}

function queue(session, operation) {
  const result = (session.agentQueue || Promise.resolve()).then(operation, operation);
  session.agentQueue = result.catch(() => {});
  return result;
}

async function waitForContents(session, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (!liveContents(session)) {
    if (session.closed) throw new Error("The browser tab was closed before it opened.");
    if (Date.now() > deadline) throw new Error("Raticode did not open the browser tab. Make sure the Raticode window is open.");
    await delay(50);
  }
}

async function prepareTab(session) {
  const contents = contentsOf(session);
  if (!session.agentPrepared || session.agentThrottled) {
    session.agentPrepared = true;
    session.agentThrottled = false;
    contents.setBackgroundThrottling(false);
  }
  await attachDebugger(session);
  await applyViewport(session);
}

async function applyViewport(session) {
  const size = session.viewSize;
  const collapsed = !size || size.width < MIN_VISIBLE_VIEWPORT.width || size.height < MIN_VISIBLE_VIEWPORT.height;
  if (collapsed === Boolean(session.viewportOverride)) return;
  if (collapsed) {
    await cdp(session, "Emulation.setDeviceMetricsOverride", {
      ...FALLBACK_VIEWPORT, deviceScaleFactor: 1, mobile: false,
    });
  } else {
    await cdp(session, "Emulation.clearDeviceMetricsOverride");
  }
  session.viewportOverride = collapsed;
}

async function cdp(session, method, params = {}) {
  return (await attachDebugger(session)).sendCommand(method, params);
}

async function attachDebugger(session) {
  const contents = contentsOf(session);
  if (!contents.debugger.isAttached()) {
    try {
      contents.debugger.attach("1.3");
    } catch (error) {
      throw new Error(`Rem cannot control this tab: ${error.message}. Close its DevTools and retry.`);
    }
    contents.debugger.once("detach", () => {
      session.viewportOverride = false;
      session.agentDialog = null;
    });
    if (!session.agentDialogListener) {
      session.agentDialogListener = (_event, method, params = {}) => {
        if (method === "Page.javascriptDialogOpening") {
          session.agentDialog = {
            type: String(params.type || "alert"),
            message: String(params.message || "").slice(0, 2000),
            ...(params.type === "prompt" ? { defaultPrompt: String(params.defaultPrompt || "").slice(0, 2000) } : {}),
          };
        } else if (method === "Page.javascriptDialogClosed") {
          session.agentDialog = null;
        }
      };
      contents.debugger.on("message", session.agentDialogListener);
    }
    // Let the page accept typing as if it had focus, without taking the
    // user's keyboard focus from wherever it is.
    await contents.debugger.sendCommand("Emulation.setFocusEmulationEnabled", { enabled: true });
    await contents.debugger.sendCommand("Page.enable").catch(() => {});
    await contents.debugger.sendCommand("DOM.enable").catch(() => {});
    await contents.debugger.sendCommand("Overlay.enable").catch(() => {});
  }
  return contents.debugger;
}

// Outlines the element Rem acts on with DevTools' overlay, which pages cannot see.
async function highlight(session, area) {
  if (!area || !Number.isFinite(area.width) || !area.width || !area.height) return;
  try {
    await cdp(session, "Overlay.highlightRect", {
      x: Math.max(0, area.x), y: Math.max(0, area.y), width: area.width, height: area.height,
      color: { r: 62, g: 232, b: 181, a: 0.18 }, outlineColor: { r: 3, g: 92, b: 103, a: 0.95 },
    });
    setTimeout(() => { void cdp(session, "Overlay.hideHighlight").catch(() => {}); }, 900).unref?.();
  } catch { /* Highlights are cosmetic. */ }
}

async function inPage(session, method, arg) {
  if (session.agentDialog) {
    throw new Error(`A ${session.agentDialog.type} dialog is open: "${session.agentDialog.message}". Call browser_handle_dialog to accept or dismiss it.`);
  }
  const code = `${PAGE_AGENT};__raticodeAgent(${JSON.stringify(method)}, ${JSON.stringify(arg ?? {})})`;
  const result = await withTimeout(
    contentsOf(session).executeJavaScriptInIsolatedWorld(AGENT_WORLD_ID, [{ code }], true),
    10000,
    "The page did not respond. It may be busy or showing a dialog. Call browser_tabs to check for a dialog.",
  );
  if (result?.error) throw new Error(result.error);
  return result?.value;
}

function targetArg(request) {
  if (typeof request.selector === "string" && request.selector.trim()) {
    return { selector: request.selector.trim().slice(0, 1000) };
  }
  if (typeof request.ref === "string" && request.ref.trim()) return { ref: request.ref.trim() };
  throw new Error("Pass an element ref from browser_snapshot, or a CSS selector.");
}

async function navigate(session, url) {
  const contents = contentsOf(session);
  session.error = "";
  session.lastBlockedDownload = null;
  try {
    await withTimeout(contents.loadURL(url), NAVIGATION_TIMEOUT_MS, "");
  } catch (error) {
    // Downloads and same-document redirects abort the load but leave a usable page.
    if (!/ERR_ABORTED/.test(String(error?.message))) {
      session.error = error.message || String(error);
      throw new Error(`Could not open ${url}: ${session.error}`);
    }
  }
}

async function settle(session) {
  await delay(150);
  const deadline = Date.now() + SETTLE_TIMEOUT_MS;
  while (liveContents(session)?.isLoading() && !session.agentDialog && Date.now() < deadline) await delay(100);
}

async function snapshot(session) {
  const contents = contentsOf(session);
  const result = await inPage(session, "snapshot");
  const header = [
    `Page URL: ${contents.getURL()}`,
    `Page title: ${contents.getTitle()}`,
    `Viewport: ${result.viewport.width}x${result.viewport.height}, scrolled to ${result.scroll.x},${result.scroll.y} of ${result.scroll.height}px`,
    "",
  ].join("\n");
  let text = header + (result.lines || "- (the page has no visible content)");
  if (result.truncated || text.length > MAX_RESULT_CHARS) {
    text = text.slice(0, MAX_RESULT_CHARS) + "\n- (snapshot truncated; scroll or use browser_evaluate for the rest)";
  }
  return { tab: session.id, snapshot: text };
}

function withWarning(result, summary) {
  return result?.covered ? { ...summary, warning: `Another element was on top: ${result.covered}` } : summary;
}

function parseKey(value) {
  const parts = String(value ?? "").split("+").map((part) => part.trim()).filter(Boolean);
  const key = String(value ?? "").endsWith("+") ? "+" : parts.pop();
  if (!key) throw new Error("Name a key, such as Enter, ArrowDown, or Control+a.");
  const pressed = { ctrlKey: false, altKey: false, shiftKey: false, metaKey: false };
  for (const part of parts) {
    if (!MODIFIERS[part]) throw new Error(`Unknown modifier ${part}. Use Alt, Control, Meta, or Shift.`);
    pressed[MODIFIERS[part]] = true;
  }
  const named = key === " " ? "Space" : key;
  const single = [...named].length === 1;
  if (!single && !(named in KEY_CODES)) throw new Error(`Unknown key ${key}.`);
  const upper = single ? named.toUpperCase() : "";
  return {
    ...pressed,
    key: named === "Space" ? " " : single && pressed.shiftKey ? upper : named,
    code: single
      ? (/[A-Z]/.test(upper) ? `Key${upper}` : /\d/.test(named) ? `Digit${named}` : "")
      : named,
    keyCode: single ? upper.charCodeAt(0) : KEY_CODES[named],
    text: single ? (pressed.shiftKey ? upper : named) : KEY_TEXT[named] ?? "",
  };
}

async function screenshot(session) {
  const contents = contentsOf(session);
  let image;
  try {
    image = await withTimeout(contents.capturePage(), 8000, HIDDEN_SCREENSHOT_MESSAGE);
  } catch (error) {
    throw new Error(error.message === HIDDEN_SCREENSHOT_MESSAGE ? error.message : `${HIDDEN_SCREENSHOT_MESSAGE} (${error.message})`);
  }
  if (image.isEmpty()) throw new Error(HIDDEN_SCREENSHOT_MESSAGE);
  const { width } = image.getSize();
  if (width > 1600) image = image.resize({ width: 1600 });
  const size = image.getSize();
  return { ...tabSummary(session), width: size.width, height: size.height, image: image.toPNG().toString("base64") };
}

async function wait(session, request) {
  const seconds = Math.min(Math.max(Number(request.time) || 0, 0), 60);
  if (!request.text && !request.textGone) {
    if (!seconds) throw new Error("Pass text, textGone, or time in seconds.");
    await delay(seconds * 1000);
    return tabSummary(session);
  }
  const deadline = Date.now() + (seconds || 10) * 1000;
  const wanted = request.text ? String(request.text) : String(request.textGone);
  const present = Boolean(request.text);
  while (true) {
    const found = await inPage(session, "hasText", { text: wanted });
    if (found === present) return tabSummary(session);
    if (Date.now() > deadline) {
      throw new Error(present ? `"${wanted}" did not appear.` : `"${wanted}" is still on the page.`);
    }
    await delay(250);
  }
}

function withTimeout(promise, ms, message) {
  let timer;
  return Promise.race([
    promise,
    new Promise((resolve, reject) => {
      // Without a message, stop waiting but let the caller continue.
      timer = setTimeout(() => (message ? reject(new Error(message)) : resolve()), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function bearer(request) {
  const header = String(request.headers.authorization || "");
  return header.startsWith("Bearer ") ? header.slice(7) : "";
}

function sameSecret(actual, expected) {
  const left = Buffer.from(String(actual));
  const right = Buffer.from(String(expected));
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

// Two routes on loopback: /browser for Rem with a capability, and
// /browser/admin for the Raticode backend, which grants and revokes them.
async function startAgentBrowserService(handle, capabilities = createCapabilityRegistry()) {
  const adminToken = crypto.randomBytes(32).toString("hex");
  let port = 0;
  const server = http.createServer(async (request, response) => {
    const reply = (status, body) => {
      response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      response.end(JSON.stringify(body));
    };
    // Browsers always send Origin on cross-site POSTs; a wrong Host means DNS rebinding.
    const validHost = request.headers.host === `127.0.0.1:${port}`;
    if (request.method !== "POST" || request.headers.origin || !validHost) {
      reply(403, { error: "Forbidden" });
      return;
    }
    const admin = request.url === "/browser/admin" && sameSecret(bearer(request), adminToken);
    const grant = request.url === "/browser" ? capabilities.resolve(bearer(request)) : null;
    if (!admin && !grant) {
      reply(403, { error: request.url === "/browser" ? "Rem's browser access ended. Turn on Browser for this thread and send a new message." : "Forbidden" });
      return;
    }
    try {
      const chunks = [];
      let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > MAX_REQUEST_BYTES) throw new Error("The browser request is too large.");
        chunks.push(chunk);
      }
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
      if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("Send a JSON object.");
      if (admin) {
        if (body.action === "grant") {
          reply(200, capabilities.grant({
            owner: body.owner,
            label: body.label,
            ttlMs: Number(body.ttlSeconds) * 1000,
            uploadRoots: body.uploadRoots,
          }));
        } else if (body.action === "revoke") {
          reply(200, { revoked: capabilities.revoke(body.capability) });
        } else {
          throw new Error("Unknown admin action.");
        }
        return;
      }
      // Identity always comes from the capability, never from the caller.
      const rest = { ...body };
      delete rest.agent;
      delete rest.label;
      delete rest.uploadRoots;
      reply(200, await handle({ ...rest, agent: grant.owner, label: grant.label, uploadRoots: grant.uploadRoots }));
    } catch (error) {
      reply(400, { error: error instanceof Error ? error.message : String(error) });
    }
  });
  server.requestTimeout = 180000;
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  port = server.address().port;
  server.unref();
  return {
    server,
    adminToken,
    capabilities,
    url: `http://127.0.0.1:${port}/browser`,
    close: () => server.close(),
  };
}

// Earlier versions published a master token here; delete it if it remains.
function removeLegacyAgentBrowserConnection(dataDir) {
  try { fs.rmSync(path.join(dataDir, LEGACY_CONNECTION_FILE), { force: true }); } catch { /* Nothing to remove. */ }
}

module.exports = {
  CLOSE_IDLE_MS,
  LEGACY_CONNECTION_FILE,
  MAX_AGENT_TABS,
  THROTTLE_IDLE_MS,
  agentUrl,
  createAgentBrowserController,
  createCapabilityRegistry,
  removeLegacyAgentBrowserConnection,
  remNeedsApproval,
  startAgentBrowserService,
  uploadPaths,
};
