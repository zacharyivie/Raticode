// Which editor the node inspector gives a Rattish contract field, and the row rules of
// the list and name/value editors. Rows are kept as plain strings so a draft can hold a
// half-typed row; parsing turns them back into the value the set_field mutation saves.

const EXACT_REFERENCE = /^\s*\{\{\s*[^{}]+?\s*\}\}\s*$/;

function schemaTypes(schema) {
  if (!schema || typeof schema !== "object") return [];
  return Array.isArray(schema.type) ? schema.type : [schema.type];
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

// "list" for an array of strings or integers, "map" for an object whose every value is a
// string, "json" for any other object or array, null for a field that isn't either.
export function rattishFieldEditorKind(schema) {
  const types = schemaTypes(schema);
  if (types.includes("array")) {
    const items = schema.items;
    const itemTypes = schemaTypes(items);
    const flat = isPlainObject(items) && !items.properties && !items.items && !items.enum;
    if (flat && itemTypes.length === 1 && (itemTypes[0] === "string" || itemTypes[0] === "integer")) {
      return "list";
    }
    return "json";
  }
  if (types.includes("object")) {
    const extra = schema.additionalProperties;
    const flat = !schema.properties && !schema.patternProperties && isPlainObject(extra);
    if (flat && schemaTypes(extra).length === 1 && schemaTypes(extra)[0] === "string" && !extra.properties) {
      return "map";
    }
    return "json";
  }
  return null;
}

// One noun per row, for the Add button and each row's control names.
const ROW_NOUNS = {
  approvers: "approver",
  args: "argument",
  email_to: "address",
  env: "variable",
  expected_statuses: "status",
  extra_paths: "path",
  headers: "header",
  mcp_servers: "MCP server",
  network_allowlist: "host",
  options: "option",
  output_mapping: "mapping",
  params: "parameter",
  reject_options: "reject option",
  secret_fields: "field",
  tools: "tool",
  variables: "variable",
};

export function rattishRowNoun(field) {
  return ROW_NOUNS[field] ?? "row";
}

export function isExactReference(value) {
  return typeof value === "string" && EXACT_REFERENCE.test(value);
}

// Whether a saved value can be shown as rows. A value that can't, such as a nested object
// someone wrote in Code, opens as JSON so nothing is lost.
export function fitsRows(kind, value, itemType = "string") {
  if (value === null || value === undefined) return true;
  if (kind === "list") {
    return Array.isArray(value) && value.every((item) =>
      itemType === "integer"
        ? Number.isInteger(item) || isExactReference(item)
        : typeof item === "string",
    );
  }
  if (kind === "map") {
    return isPlainObject(value) && Object.values(value).every((item) => typeof item === "string");
  }
  return false;
}

export function listRowsFromValue(value) {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

export function mapRowsFromValue(value) {
  return isPlainObject(value)
    ? Object.entries(value).map(([name, item]) => [name, String(item ?? "")])
    : [];
}

// rows: strings. Blank rows are left out. Returns { ok, value, rowErrors, error }, where
// rowErrors[i] says what is wrong with row i.
export function parseListRows(rows, { itemType = "string", minimum, maximum, nullable = false, unique = false } = {}) {
  const value = [];
  const rowErrors = rows.map(() => "");
  const seen = new Map();
  rows.forEach((row, index) => {
    const text = String(row ?? "");
    const trimmed = text.trim();
    if (!trimmed) return;
    let item = text;
    if (isExactReference(trimmed)) {
      item = trimmed;
    } else if (itemType === "integer") {
      if (!/^[+-]?\d+$/.test(trimmed)) {
        rowErrors[index] = "Enter a whole number or an exact {{...}} reference.";
        return;
      }
      item = Number(trimmed);
      if ((minimum !== undefined && item < minimum) || (maximum !== undefined && item > maximum)) {
        rowErrors[index] =
          minimum !== undefined && maximum !== undefined
            ? `Enter a number from ${minimum} to ${maximum}.`
            : minimum !== undefined
              ? `Enter ${minimum} or greater.`
              : `Enter ${maximum} or less.`;
        return;
      }
    }
    const key = typeof item === "number" ? `n:${item}` : `s:${item}`;
    if (unique && seen.has(key)) {
      rowErrors[index] = `Already listed in row ${seen.get(key) + 1}.`;
      return;
    }
    seen.set(key, index);
    value.push(item);
  });
  const failed = rowErrors.findIndex(Boolean);
  if (failed >= 0) {
    return { ok: false, error: `Row ${failed + 1}: ${rowErrors[failed]}`, rowErrors };
  }
  return { ok: true, value: nullable && !value.length ? null : value, rowErrors };
}

// rows: [name, value] pairs. A row with neither is left out; a value with no name, or a
// name used twice, is flagged on its row.
export function parseMapRows(rows) {
  const value = {};
  const rowErrors = rows.map(() => "");
  const seen = new Map();
  rows.forEach(([rawName, rawValue], index) => {
    const name = String(rawName ?? "").trim();
    const item = String(rawValue ?? "");
    if (!name && !item.trim()) return;
    if (!name) {
      rowErrors[index] = "Add a name for this value.";
      return;
    }
    if (seen.has(name)) {
      rowErrors[index] = `${name} is already used in row ${seen.get(name) + 1}.`;
      return;
    }
    seen.set(name, index);
    value[name] = item;
  });
  const failed = rowErrors.findIndex(Boolean);
  if (failed >= 0) {
    return { ok: false, error: `Row ${failed + 1}: ${rowErrors[failed]}`, rowErrors };
  }
  return { ok: true, value, rowErrors };
}

// Row edits that keep the rest of the list in order.
export function moveRow(rows, index, offset) {
  const target = index + offset;
  if (target < 0 || target >= rows.length) return rows;
  const next = rows.slice();
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function removeRow(rows, index) {
  return rows.filter((_, position) => position !== index);
}
