// Row editors for Rattish list fields (approvers, args, tools) and name/value fields
// (headers, env, variables). Each one has an Edit as JSON switch that opens the same
// JSON box every other object field uses, so a value the rows can't show is never lost.
import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Plus, X } from "lucide-react";
import {
  DraftFieldMessage,
  JsonBodyField,
  TextField,
  useCommittedDraft,
  useInspectorDraftScope,
} from "./InspectorFields.jsx";
import {
  fitsRows,
  isExactReference,
  listRowsFromValue,
  mapRowsFromValue,
  moveRow,
  parseListRows,
  parseMapRows,
  rattishFieldEditorKind,
  rattishRowNoun,
  removeRow,
} from "../lib/rattishFieldEditors.js";

const ROW_INPUT =
  "h-8 w-full min-w-0 rounded-md border bg-white px-2 text-xs text-ink outline-none transition focus:ring-2 focus:ring-brand/15";
const ICON_BUTTON =
  "grid h-8 w-7 shrink-0 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-transparent dark:hover:bg-[#2a2a2a]";

function rowInputClass(error, value) {
  return `${ROW_INPUT} ${isExactReference(value) ? "font-mono" : ""} ${
    error ? "border-red-400 focus:border-red-500" : "border-line focus:border-brand"
  }`;
}

function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function readRows(text) {
  try {
    const rows = JSON.parse(text);
    return Array.isArray(rows) ? rows : [];
  } catch {
    return [];
  }
}

// One list or name/value field: rows by default, JSON when switched or when the saved
// value has a shape the rows can't show.
export function RattishRowsField({ description, field, label, onChange, schema, value }) {
  const kind = rattishFieldEditorKind(schema);
  const types = Array.isArray(schema?.type) ? schema.type : [schema?.type];
  const itemType = kind === "list" ? schema.items.type : "string";
  const wholeReference = isExactReference(value);
  const fits = wholeReference || fitsRows(kind, value, itemType);
  const scope = useInspectorDraftScope();
  // The switch belongs to the node it was turned on for.
  const [mode, setMode] = useState({ scope, json: false });
  const asJson = !fits || (mode.scope === scope && mode.json);
  const switchId = useId();
  const helpId = useId();
  const help = typeof description === "string" ? description.trim() : "";

  const noun = rattishRowNoun(field);
  return (
    <fieldset
      aria-describedby={help ? helpId : undefined}
      className="min-w-0 space-y-1.5"
      data-rattish-rows={kind}
    >
      <legend className="sr-only">{label}</legend>
      <div className="flex items-center justify-between gap-2">
        <span aria-hidden="true" className="text-xs font-medium text-muted">{label}</span>
        <span className="flex items-center gap-1.5">
          <label className="text-[11px] text-muted" htmlFor={switchId}>
            Edit as JSON
          </label>
          <button
            aria-checked={asJson}
            aria-disabled={!fits || undefined}
            aria-label={`Edit ${label} as JSON`}
            className={`relative h-4 w-7 shrink-0 rounded-full transition ${
              asJson ? "bg-brand" : "bg-slate-300 dark:bg-[#3a3a3a]"
            } ${fits ? "" : "cursor-not-allowed opacity-60"}`}
            id={switchId}
            role="switch"
            title={fits ? "Edit as JSON" : "This value only fits the JSON editor"}
            type="button"
            onClick={() => {
              if (fits) setMode({ scope, json: !asJson });
            }}
          >
            <span
              aria-hidden="true"
              className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-all ${
                asJson ? "left-3.5" : "left-0.5"
              }`}
            />
          </button>
        </span>
      </div>
      {help ? (
        <p className="-mt-1 text-[11px] leading-4 text-muted" id={helpId}>
          {help}
        </p>
      ) : null}
      {asJson ? (
        <>
          <JsonBodyField
            label={<span className="sr-only">{label} as JSON</span>}
            value={value ?? (types.includes("array") ? [] : {})}
            onChange={onChange}
          />
          {!fits ? (
            <p className="text-[11px] leading-4 text-muted">
              This value has parts the {kind === "map" ? "name and value" : "list"} rows can&apos;t
              show, so it opens as JSON.
            </p>
          ) : null}
        </>
      ) : wholeReference ? (
        <>
          <TextField commitOnBlur label="Reference" value={value} onChange={onChange} />
          <p className="text-[11px] leading-4 text-muted">
            The whole value comes from this reference when the node runs.
          </p>
        </>
      ) : kind === "list" ? (
        <ListRows
          itemType={itemType}
          label={label}
          noun={noun}
          nullable={types.includes("null")}
          schema={schema}
          value={value}
          onChange={onChange}
        />
      ) : (
        <NameValueRows label={label} noun={noun} value={value} onChange={onChange} />
      )}
    </fieldset>
  );
}

// Focus that follows a row after Add, Remove or Move, so the keyboard stays in place.
function useRowFocus() {
  const controls = useRef(new Map());
  const pending = useRef(null);
  useEffect(() => {
    const wanted = pending.current;
    if (!wanted) return;
    pending.current = null;
    for (const key of wanted) {
      const element = controls.current.get(key);
      if (element && !element.disabled && !element.hasAttribute?.("disabled")) {
        element.focus?.();
        return;
      }
    }
  });
  return {
    ref(key) {
      return (element) => {
        if (element) controls.current.set(key, element);
        else controls.current.delete(key);
      };
    },
    focusNext(...keys) {
      pending.current = keys;
    },
  };
}

// Shared draft handling: the rows live in one useCommittedDraft text, so scope changes,
// edits made elsewhere, refused saves and Escape work as they do in every other field.
function useRowsDraft({ format, onChange, parse, value }) {
  const containerRef = useRef(null);
  const state = useCommittedDraft({ format, onCommit: onChange, parse, value });
  const focusProps = {
    onBlur(event) {
      const next = event?.relatedTarget;
      if (next && containerRef.current?.contains?.(next)) return;
      state.onBlur();
    },
    onFocus: state.onFocus,
  };
  function edit(rows) {
    state.onChange(JSON.stringify(rows));
  }
  function save(rows) {
    const text = JSON.stringify(rows);
    state.onChange(text);
    state.commitText(text);
  }
  return { containerRef, edit, focusProps, save, state };
}

function ListRows({ itemType, label, noun, nullable, onChange, schema, value }) {
  const options = {
    itemType,
    maximum: schema.items?.maximum,
    minimum: schema.items?.minimum,
    nullable,
    unique: Boolean(schema.uniqueItems),
  };
  const { containerRef, edit, focusProps, save, state } = useRowsDraft({
    format: (saved) => JSON.stringify(listRowsFromValue(saved)),
    onChange,
    parse: (text) => parseListRows(readRows(text), options),
    value,
  });
  const rows = readRows(state.draft);
  const { rowErrors } = parseListRows(rows, options);
  const focus = useRowFocus();
  const messageId = useId();
  const Noun = capitalize(noun);

  // Focus follows the moved row; at the top or bottom, where its own button is now
  // disabled, it goes to the row's other move button.
  function move(index, offset) {
    const target = index + offset;
    const atEdge = target === 0 || target === rows.length - 1;
    const control = offset < 0 ? (atEdge ? "down" : "up") : atEdge ? "up" : "down";
    focus.focusNext(`${target}:${control}`);
    save(moveRow(rows, index, offset));
  }
  function remove(index) {
    focus.focusNext(`${index}:remove`, `${index - 1}:remove`, "add");
    save(removeRow(rows, index));
  }

  return (
    <div ref={containerRef} className="space-y-1.5">
      {rows.length ? (
        <ol className="space-y-1">
          {rows.map((row, index) => {
            const errorId = `${messageId}-${index}`;
            return (
              <li key={index}>
                <div className="flex items-center gap-0.5">
                  <input
                    ref={focus.ref(`${index}:value`)}
                    aria-describedby={rowErrors[index] ? errorId : undefined}
                    aria-invalid={Boolean(rowErrors[index]) || undefined}
                    aria-label={`${Noun} ${index + 1}`}
                    className={`${rowInputClass(rowErrors[index], row)} mr-0.5`}
                    inputMode={itemType === "integer" && !isExactReference(row) ? "numeric" : undefined}
                    spellCheck={false}
                    type="text"
                    value={row}
                    onChange={(event) => edit(rows.map((item, position) => (position === index ? event.target.value : item)))}
                    onKeyDown={state.onKeyDown}
                    {...focusProps}
                  />
                  <button
                    ref={focus.ref(`${index}:up`)}
                    aria-label={`Move ${noun} ${index + 1} up`}
                    className={ICON_BUTTON}
                    disabled={index === 0}
                    title="Move up"
                    type="button"
                    onClick={() => move(index, -1)}
                    {...focusProps}
                  >
                    <ChevronUp size={14} />
                  </button>
                  <button
                    ref={focus.ref(`${index}:down`)}
                    aria-label={`Move ${noun} ${index + 1} down`}
                    className={ICON_BUTTON}
                    disabled={index === rows.length - 1}
                    title="Move down"
                    type="button"
                    onClick={() => move(index, 1)}
                    {...focusProps}
                  >
                    <ChevronDown size={14} />
                  </button>
                  <button
                    ref={focus.ref(`${index}:remove`)}
                    aria-label={`Remove ${noun} ${index + 1}`}
                    className={`${ICON_BUTTON} hover:text-red-600`}
                    title="Remove"
                    type="button"
                    onClick={() => remove(index)}
                    {...focusProps}
                  >
                    <X size={14} />
                  </button>
                </div>
                {rowErrors[index] ? (
                  <p id={errorId} className="mt-0.5 text-[11px] leading-4 text-red-600">
                    {rowErrors[index]}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="text-xs text-muted">No {label.toLowerCase()} yet.</p>
      )}
      <AddRowButton
        buttonRef={focus.ref("add")}
        focusProps={focusProps}
        noun={noun}
        onClick={() => {
          focus.focusNext(`${rows.length}:value`);
          edit([...rows, ""]);
        }}
      />
      <DraftRowsMessage state={state} />
    </div>
  );
}

function NameValueRows({ label, noun, onChange, value }) {
  const { containerRef, edit, focusProps, save, state } = useRowsDraft({
    format: (saved) => JSON.stringify(mapRowsFromValue(saved)),
    onChange,
    parse: (text) => parseMapRows(readRows(text)),
    value,
  });
  const rows = readRows(state.draft);
  const { rowErrors } = parseMapRows(rows);
  const focus = useRowFocus();
  const messageId = useId();
  const Noun = capitalize(noun);

  function change(index, column, text) {
    edit(rows.map((row, position) => {
      if (position !== index) return row;
      const next = row.slice();
      next[column] = text;
      return next;
    }));
  }
  function remove(index) {
    focus.focusNext(`${index}:remove`, `${index - 1}:remove`, "add");
    save(removeRow(rows, index));
  }

  return (
    <div ref={containerRef} className="space-y-1.5">
      {rows.length ? (
        <div className="space-y-1">
          <div aria-hidden="true" className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_1.75rem] gap-1 px-0.5 text-[11px] font-medium text-muted">
            <span>Name</span>
            <span>Value</span>
          </div>
          {rows.map(([name, item], index) => {
            const errorId = `${messageId}-${index}`;
            return (
              <div key={index}>
                <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_1.75rem] items-center gap-1">
                  <input
                    ref={focus.ref(`${index}:name`)}
                    aria-describedby={rowErrors[index] ? errorId : undefined}
                    aria-invalid={Boolean(rowErrors[index]) || undefined}
                    aria-label={`${Noun} ${index + 1} name`}
                    autoCapitalize="off"
                    className={rowInputClass(rowErrors[index], name)}
                    placeholder="Name"
                    spellCheck={false}
                    type="text"
                    value={name}
                    onChange={(event) => change(index, 0, event.target.value)}
                    onKeyDown={state.onKeyDown}
                    {...focusProps}
                  />
                  <input
                    ref={focus.ref(`${index}:value`)}
                    aria-label={`${Noun} ${index + 1} value`}
                    className={rowInputClass("", item)}
                    placeholder="Value"
                    spellCheck={false}
                    type="text"
                    value={item}
                    onChange={(event) => change(index, 1, event.target.value)}
                    onKeyDown={state.onKeyDown}
                    {...focusProps}
                  />
                  <button
                    ref={focus.ref(`${index}:remove`)}
                    aria-label={`Remove ${noun} ${index + 1}`}
                    className={`${ICON_BUTTON} hover:text-red-600`}
                    title="Remove"
                    type="button"
                    onClick={() => remove(index)}
                    {...focusProps}
                  >
                    <X size={14} />
                  </button>
                </div>
                {rowErrors[index] ? (
                  <p id={errorId} className="mt-0.5 text-[11px] leading-4 text-red-600">
                    {rowErrors[index]}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-xs text-muted">No {label.toLowerCase()} yet.</p>
      )}
      <AddRowButton
        buttonRef={focus.ref("add")}
        focusProps={focusProps}
        noun={noun}
        onClick={() => {
          focus.focusNext(`${rows.length}:name`);
          edit([...rows, ["", ""]]);
        }}
      />
      <DraftRowsMessage state={state} />
    </div>
  );
}

function AddRowButton({ buttonRef, focusProps, noun, onClick }) {
  return (
    <button
      ref={buttonRef}
      className="inline-flex h-7 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-indigo-700 transition hover:bg-indigo-50 dark:hover:bg-[#2a2a2a]"
      type="button"
      onClick={onClick}
      {...focusProps}
    >
      <Plus size={13} />
      {`Add ${noun}`}
    </button>
  );
}

// Row problems already show under their rows; this keeps the saved-value and
// changed-elsewhere notices every draft field has.
function DraftRowsMessage({ state }) {
  const id = useId();
  return <DraftFieldMessage id={id} state={state} />;
}
