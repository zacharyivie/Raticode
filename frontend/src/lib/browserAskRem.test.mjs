import assert from "node:assert/strict";
import test from "node:test";
import { askRemPageText } from "./browserAskRem.js";

test("Ask Rem text leads with the right-clicked element, then the selection and page", () => {
  const text = askRemPageText({
    selection: "Buy",
    text: "Shop page",
    element: { tag: "button", path: "main > button#buy", label: "Buy now", alt: "", href: "", text: "Buy", html: "<button id=\"buy\">Buy</button>" },
  });
  assert.equal(text, [
    "The user right-clicked this element and is asking about it:\nElement: <button>\nLocation: main > button#buy\nLabel: Buy now\nText:\nBuy\nHTML:\n<button id=\"buy\">Buy</button>",
    "Selected text:\nBuy",
    "Page text:\nShop page",
  ].join("\n\n"));
  assert.equal(askRemPageText({ text: "Shop page" }), "Shop page");
});
