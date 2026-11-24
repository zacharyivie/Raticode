import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";

// ESLint 10 tracks JSX references itself, so no-undef and no-unused-vars cover
// what eslint-plugin-react's jsx-no-undef, jsx-uses-vars and jsx-uses-react did.
// These selectors keep the rest of that plugin's recommended checks that matter
// here; electron/tests/eslint-react-rules.test.cjs plants each mistake.
// esquery's :has() searches all descendants, so the key check looks only inside
// the opening tag, where the only descendants are its attributes.
const unkeyed = [
  "JSXElement > JSXOpeningElement:not(:has(JSXAttribute[name.name='key']))",
  "JSXFragment",
];
const mapCallback =
  "CallExpression[callee.property.name=/^(map|flatMap)$/] > :matches(ArrowFunctionExpression, FunctionExpression)";
const keyMessage = "Missing \"key\" prop for element in iterator (was react/jsx-key).";
export const reactSyntaxRules = [
  ...unkeyed.flatMap((element) => [
    `${mapCallback} > ${element}`,
    `${mapCallback} > BlockStatement > ReturnStatement > ${element}`,
    `${mapCallback} > :matches(ConditionalExpression, LogicalExpression) > ${element}`,
    `ArrayExpression > ${element}`,
  ]).map((selector) => ({ selector, message: keyMessage })),
  {
    selector: "JSXOpeningElement[name.name=/^[a-z]/] > JSXAttribute[name.name=/^(class|for|charset|tabindex|readonly|maxlength|autocomplete|autofocus|contenteditable|crossorigin|frameborder|allowfullscreen|enctype|srcset|colspan|rowspan|accesskey|novalidate|spellcheck|onclick|onchange|oninput|onsubmit|onkeydown|onkeyup)$/]",
    message: "Unknown DOM property: use the React name, such as className or htmlFor (was react/no-unknown-property).",
  },
  {
    selector: "JSXOpeningElement:has(JSXAttribute[name.name='target'][value.value='_blank']):has(JSXAttribute[name.name='href']):not(:has(JSXAttribute[name.name='rel']:matches([value.value=/\\bnoreferrer\\b/], [value.type='JSXExpressionContainer'])))",
    message: "target=\"_blank\" links need rel=\"noreferrer\" (was react/jsx-no-target-blank).",
  },
  {
    selector: "JSXAttribute[name.name='children']",
    message: "Pass children as JSX children, not as a prop (was react/no-children-prop).",
  },
  {
    selector: "CallExpression[callee.property.name='createElement'] > ObjectExpression > Property[key.name='children']",
    message: "Pass children as createElement arguments, not as a prop (was react/no-children-prop).",
  },
  {
    selector: "JSXOpeningElement[selfClosing=false]:has(JSXAttribute[name.name='dangerouslySetInnerHTML'])",
    message: "Use children or dangerouslySetInnerHTML, not both (was react/no-danger-with-children).",
  },
  {
    selector: "JSXText[value=/^\\s*\\/(\\/|\\*)/m]",
    message: "Comments inside JSX children must be in braces (was react/jsx-no-comment-textnodes).",
  },
  {
    selector: "JSXAttribute[name.name='ref'] > Literal",
    message: "String refs are removed in React 19; use useRef or a callback ref (was react/no-string-refs).",
  },
  {
    selector: "AssignmentExpression > MemberExpression.left[object.type='MemberExpression'][object.object.type='ThisExpression'][object.property.name='state']",
    message: "Do not mutate this.state; call setState (was react/no-direct-mutation-state).",
  },
  {
    selector: "MemberExpression[property.name=/^(findDOMNode|isMounted|UNSAFE_componentWillMount|UNSAFE_componentWillReceiveProps|UNSAFE_componentWillUpdate|componentWillMount|componentWillReceiveProps|componentWillUpdate)$/], MethodDefinition[key.name=/^(UNSAFE_)?componentWill(Mount|ReceiveProps|Update)$/]",
    message: "Deprecated React API (was react/no-deprecated, no-find-dom-node or no-is-mounted).",
  },
];

export default [
  {
    ignores: [
      "build/**",
      "coverage/**",
      "dist/**",
      "perf-dist/**",
      "out/**",
      "release/**",
      "**/*.bundle.js",
      "**/*.generated.js",
      "**/*.min.js",
    ],
  },
  js.configs.recommended,
  {
    files: ["src/**/*.{js,jsx}", "benchmarks/*.jsx"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
      },
      globals: {
        ...globals.browser,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      // react-hooks 7 adds React Compiler rules to "recommended". The studio does
      // not use the compiler, so keep the classic hook rules.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "no-restricted-syntax": ["error", ...reactSyntaxRules],
    },
  },
  {
    files: [
      "*.config.js",
      "benchmarks/*.{js,cjs}",
      "browser-tests/run-*.cjs",
      "build-resources/*.cjs",
      "electron/**/*.{cjs,js}",
    ],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },
  {
    files: ["browser-tests/studio-preload-mock.cjs"],
    languageOptions: {
      globals: {
        ...globals.browser,
      },
    },
  },
  {
    files: ["src/**/*.test.{js,mjs}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
  },
];
