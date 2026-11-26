import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  fitsRows,
  moveRow,
  parseListRows,
  parseMapRows,
  rattishFieldEditorKind,
  rattishRowNoun,
} from "./rattishFieldEditors.js";

const contractsDir = path.resolve(import.meta.dirname, "../../../rattish/contracts");

function contractFields() {
  const fields = {};
  for (const name of fs.readdirSync(contractsDir).filter((file) => file.endsWith(".json"))) {
    const contract = JSON.parse(fs.readFileSync(path.join(contractsDir, name), "utf8"));
    for (const [field, schema] of Object.entries(contract.configuration_schema?.properties ?? {})) {
      fields[`${contract.node_type}.${field}`] = schema;
    }
  }
  return fields;
}

test("string and integer arrays get the list editor, string maps the name/value editor, nested objects JSON", () => {
  const fields = contractFields();
  const kinds = (names) => Object.fromEntries(names.map((name) => {
    assert.ok(fields[name], `${name} is in the node contracts`);
    return [name, rattishFieldEditorKind(fields[name])];
  }));

  const lists = [
    "approval-gate.approvers", "approval-gate.options", "approval-gate.reject_options",
    "shell-script.args", "python-script.args", "open-resource.args",
    "agent.tools", "agent.mcp_servers", "agent.extra_paths",
    "http-request.network_allowlist", "notification.network_allowlist", "read-web-page.network_allowlist",
    "http-request.secret_fields", "notification.email_to",
    "http-request.expected_statuses", "notification.expected_statuses",
  ];
  const maps = [
    "http-request.headers", "http-request.params", "http-request.output_mapping",
    "notification.headers",
    "agent.env", "bash-command.env", "python-script.env", "shell-script.env",
    "prompt-file.variables", "jev.options",
  ];
  const json = [
    "http-request.retry", "notification.retry", "jev.criteria", "loop.source",
    "agent.output_schema", "common-llm-task.output_schema", "mcp-tool.output_schema",
    "mcp-tool.arguments", "agent.fallback", "common-llm-task.fallback",
  ];
  assert.deepEqual(kinds(lists), Object.fromEntries(lists.map((name) => [name, "list"])));
  assert.deepEqual(kinds(maps), Object.fromEntries(maps.map((name) => [name, "map"])));
  assert.deepEqual(kinds(json), Object.fromEntries(json.map((name) => [name, "json"])));

  // Plain text, numbers and switches keep their own fields.
  assert.equal(rattishFieldEditorKind(fields["http-request.url"]), null);
  assert.equal(rattishFieldEditorKind(fields["approval-gate.notify"]), null);
  // A map of objects, or a list of lists, is nested and stays JSON.
  assert.equal(rattishFieldEditorKind({ type: "object", additionalProperties: { type: "object" } }), "json");
  assert.equal(rattishFieldEditorKind({ type: "array", items: { type: "array", items: { type: "string" } } }), "json");
  assert.equal(rattishFieldEditorKind({ type: "array", items: { type: ["string", "integer"] } }), "json");
});

test("list rows drop blanks, keep references and flag bad or repeated rows", () => {
  assert.deepEqual(parseListRows(["ann", "", "  ", "bo"]), { ok: true, value: ["ann", "bo"], rowErrors: ["", "", "", ""] });
  assert.deepEqual(parseListRows([], { nullable: true }).value, null);
  assert.deepEqual(parseListRows([], {}).value, []);

  const statuses = { itemType: "integer", minimum: 100, maximum: 599, unique: true };
  assert.deepEqual(parseListRows(["200", " {{ code }} "], statuses).value, [200, "{{ code }}"]);
  const bad = parseListRows(["200", "abc", "700", "200"], statuses);
  assert.equal(bad.ok, false);
  assert.deepEqual(bad.rowErrors, [
    "",
    "Enter a whole number or an exact {{...}} reference.",
    "Enter a number from 100 to 599.",
    "Already listed in row 1.",
  ]);
  assert.equal(bad.error, "Row 2: Enter a whole number or an exact {{...}} reference.");

  assert.deepEqual(moveRow(["a", "b", "c"], 2, -1), ["a", "c", "b"]);
  assert.deepEqual(moveRow(["a", "b"], 0, -1), ["a", "b"]);
});

test("name/value rows flag empty and repeated names on their own row", () => {
  assert.deepEqual(parseMapRows([["Accept", "application/json"], ["", ""]]), {
    ok: true,
    value: { Accept: "application/json" },
    rowErrors: ["", ""],
  });
  // A whole {{...}} reference is a value like any other text.
  assert.deepEqual(parseMapRows([["Authorization", "{{token}}"]]).value, { Authorization: "{{token}}" });
  const bad = parseMapRows([["Accept", "a"], ["", "orphan"], ["Accept", "b"]]);
  assert.equal(bad.ok, false);
  assert.deepEqual(bad.rowErrors, ["", "Add a name for this value.", "Accept is already used in row 1."]);
});

test("values the rows can't show open as JSON", () => {
  assert.equal(fitsRows("map", { Accept: "x" }), true);
  assert.equal(fitsRows("map", { nested: { a: 1 } }), false);
  assert.equal(fitsRows("list", ["a", "b"]), true);
  assert.equal(fitsRows("list", [{ a: 1 }]), false);
  assert.equal(fitsRows("list", [200, "{{code}}"], "integer"), true);
  assert.equal(fitsRows("list", ["two hundred"], "integer"), false);
  assert.equal(fitsRows("list", null), true);
  assert.equal(rattishRowNoun("headers"), "header");
  assert.equal(rattishRowNoun("unknown_field"), "row");
});
