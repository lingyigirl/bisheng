/**
 * F061 T-40 regression: `ChatInput` must actually WIRE the source summary.
 *
 * This exists because the bug it catches shipped through every gate:
 *
 * - `ChatInput.tsx` carries a legacy `// @ts-strict-ignore` header, so
 *   `tsc-strict` does not check the file at all — `pnpm typecheck` was green.
 * - Vite does not typecheck, so `vite build` exited 0 and emitted a bundle
 *   containing a bare, undeclared `AppSourceSummary` reference.
 * - `AppSourceSummary.test.tsx` was green — because it imports the component
 *   directly. It tests the component, never the wiring.
 *
 * So the assertion has to be made against `ChatInput`, not against the
 * component. Importing `ChatInput` is the whole point: if the import is missing
 * again, this file fails to resolve or fails to render, rather than passing
 * quietly.
 *
 * Deliberately NOT a full render of ChatInput: that drags in Recoil atoms, the
 * workbench-models query, the file-drop hook and the speech-to-text store — a
 * lot of scaffolding to prove one import exists, and each piece is a reason for
 * this test to break for unrelated causes. The static check is exact for the
 * defect at hand.
 */

import { readFileSync } from 'fs';
import { join } from 'path';

const CHAT_INPUT = join(__dirname, 'ChatInput.tsx');

describe('ChatInput wiring (F061 T-40)', () => {
  it('imports AppSourceSummary rather than only using it', () => {
    const source = readFileSync(CHAT_INPUT, 'utf-8');

    // Used...
    expect(source).toMatch(/<AppSourceSummary\b/);

    // ...and imported. `<AppSourceSummary` without a matching import is exactly
    // the defect: TypeScript would catch it, but this file is
    // `@ts-strict-ignore` and Vite never typechecks, so nothing did.
    expect(source).toMatch(
      /import\s*\{[^}]*\bAppSourceSummary\b[^}]*\}\s*from\s*['"][^'"]+['"]/,
    );
  });

  it('imports it from the module that actually exports it', () => {
    // Guards a subtler regression: importing the right NAME from the wrong place
    // resolves to undefined at runtime and renders nothing, which looks like the
    // feature simply not appearing.
    const source = readFileSync(CHAT_INPUT, 'utf-8');
    const match = source.match(
      /import\s*\{[^}]*\bAppSourceSummary\b[^}]*\}\s*from\s*['"]([^'"]+)['"]/,
    );

    expect(match).not.toBeNull();
    const specifier = match![1];
    expect(specifier).toContain('AppSourceSummary');

    // And that path resolves, with the named export present.
    const modulePath = join(__dirname, specifier.replace(/^\.\//, './'));
    const moduleSource = readFileSync(`${modulePath}.tsx`, 'utf-8');
    expect(moduleSource).toMatch(/export\s+function\s+AppSourceSummary\b/);
  });
});
