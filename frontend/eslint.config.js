import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
    { ignores: ["node_modules", "test-results", "playwright-report"] },
    js.configs.recommended,
    ...tseslint.configs.recommended,
    {
        files: ["**/*.{ts,tsx}"],
        languageOptions: { globals: { ...globals.browser, ...globals.node } },
        plugins: { "react-hooks": reactHooks },
        rules: {
            ...reactHooks.configs.recommended.rules,
            "no-restricted-syntax": [
                "error",
                {
                    selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
                    message: "XSS risk: never inject HTML.",
                },
            ],
        },
    },
    { files: ["**/*.mjs"], languageOptions: { globals: globals.node } },
);
