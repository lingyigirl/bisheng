/**
 * Vite's `?raw` suffix is core (no plugin required): `import doc from
 * "./doc.md?raw"` yields the file contents as a string. The app has no
 * vite/client types, so declare the one suffix we use — typed as `string`
 * rather than `any` so the strict typecheck stays meaningful.
 */
declare module "*.md?raw" {
    const content: string;
    export default content;
}
