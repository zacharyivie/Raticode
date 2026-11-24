// The frontend dropped eslint-plugin-react for ESLint 10. ESLint 10 tracks JSX
// references, and eslint.config.js keeps the plugin's other recommended checks as
// no-restricted-syntax selectors. Each case plants one mistake the plugin caught.
const assert = require("node:assert/strict");
const path = require("node:path");
const { test } = require("node:test");

const frontend = path.join(__dirname, "..", "..");

async function lint(code) {
  const { ESLint } = await import("eslint");
  const eslint = new ESLint({ cwd: frontend });
  const [result] = await eslint.lintText(code, { filePath: path.join(frontend, "src/components/LintProbe.jsx") });
  return result.messages.map((message) => `${message.ruleId}: ${message.message}`);
}

const HEADER = "import { Thing } from \"./Thing.jsx\";\nconst items = [];\n";

const PLANTED = [
  ["react/jsx-key arrow", "export const A = () => <ul>{items.map((item) => <li>{item}</li>)}</ul>;", /was react\/jsx-key/],
  ["react/jsx-key return", "export const A = () => <ul>{items.map(function (item) { return <li>{item}</li>; })}</ul>;", /was react\/jsx-key/],
  ["react/jsx-key fragment", "export const A = () => <ul>{items.map((item) => <>{item}</>)}</ul>;", /was react\/jsx-key/],
  ["react/jsx-key array", "export const A = () => <ul>{[<li>a</li>, <li key=\"b\">b</li>]}</ul>;", /was react\/jsx-key/],
  ["react/jsx-key conditional", "export const A = () => <ul>{items.map((item) => item ? <li>{item}</li> : null)}</ul>;", /was react\/jsx-key/],
  ["react/no-unknown-property class", "export const A = () => <div class=\"x\" />;", /was react\/no-unknown-property/],
  ["react/no-unknown-property for", "export const A = () => <label for=\"x\">X</label>;", /was react\/no-unknown-property/],
  ["react/jsx-no-target-blank", "export const A = () => <a href=\"https://example.com\" target=\"_blank\">x</a>;", /was react\/jsx-no-target-blank/],
  ["react/jsx-no-target-blank weak rel", "export const A = () => <a href=\"https://example.com\" target=\"_blank\" rel=\"noopener\">x</a>;", /was react\/jsx-no-target-blank/],
  ["react/no-children-prop", "export const A = () => <Thing children=\"x\" />;", /was react\/no-children-prop/],
  ["react/no-danger-with-children", "export const A = () => <div dangerouslySetInnerHTML={{ __html: \"x\" }}>child</div>;", /was react\/no-danger-with-children/],
  ["react/jsx-no-comment-textnodes", "export const A = () => <div>// not a comment</div>;", /was react\/jsx-no-comment-textnodes/],
  ["react/no-string-refs", "export const A = () => <div ref=\"box\" />;", /was react\/no-string-refs/],
  ["react/no-direct-mutation-state", "export class A { bump() { this.state.count = 1; } }", /was react\/no-direct-mutation-state/],
  ["react/no-deprecated lifecycle", "export class A { componentWillMount() {} }", /no-deprecated/],
  ["react/no-find-dom-node", "export const A = (dom, node) => dom.findDOMNode(node);", /no-find-dom-node/],
  // ESLint 10 core covers these three without the plugin.
  ["react/jsx-no-undef", "export const A = () => <Missing />;", /^no-undef: 'Missing' is not defined/],
  ["react/jsx-uses-vars", "import { Unused } from \"./Unused.jsx\";\nexport const A = () => <div />;", /^no-unused-vars: 'Unused' is defined/],
];

for (const [name, code, expected] of PLANTED) {
  test(`lint still reports ${name}`, async () => {
    const messages = await lint(HEADER + code + "\nexport const B = () => <Thing />;\n");
    assert.ok(messages.some((message) => expected.test(message)), messages.join("\n") || "no lint messages");
  });
}

test("correct React code passes the replacement checks", async () => {
  const messages = await lint(`${HEADER}
export function A({ html }) {
  return <section className="x">
    <label htmlFor="x">X</label>
    <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
    <ul>{items.map((item) => { return <li key={item}><span>{item}</span></li>; })}</ul>
    <ul>{items.map((item) => item ? <Thing key={item} /> : null)}</ul>
    <ul>{[<li key="a">a</li>, <li key="b">b</li>]}</ul>
    <a href="https://example.com" target="_blank" rel="noreferrer">x</a>
    <a href="https://example.com" target="_blank" rel={["noopener", "noreferrer"].join(" ")}>x</a>
    <div dangerouslySetInnerHTML={{ __html: html }} />
    <Thing>{/* a real comment */}child</Thing>
  </section>;
}
`);
  assert.deepEqual(messages, []);
});
