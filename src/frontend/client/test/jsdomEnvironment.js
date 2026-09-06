/**
 * jsdom environment that disables the optional `canvas` dependency.
 *
 * The pnpm-installed canvas@2.11.2 has no native binding on this machine, and
 * jsdom's utils.js probes `require.resolve("canvas")` at load time — with the
 * package present the probe succeeds and the follow-up `require("canvas")`
 * throws from inside lib/bindings.js, killing every test suite before it runs.
 *
 * By making the probe throw, jsdom takes its built-in "canvas is not
 * installed" path (exports.Canvas stays null) and the environment loads
 * normally. The monkey-patch is installed BEFORE jest-environment-jsdom is
 * required (it pulls jsdom in at module scope) and is never restored — the
 * whole jest process runs without canvas, which is exactly the desired state
 * for unit tests (see also jest-canvas-mock in setupTests.js).
 */
'use strict';

const Module = require('module');
const _resolveFilename = Module._resolveFilename;

Module._resolveFilename = function (request, ...args) {
  if (request === 'canvas') {
    const err = new Error(`canvas disabled in tests: ${request}`);
    err.code = 'MODULE_NOT_FOUND';
    throw err;
  }
  return _resolveFilename.call(this, request, ...args);
};

module.exports = require('jest-environment-jsdom');
