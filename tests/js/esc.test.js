// Phase 2.1: escaping regression test for the renderer's esc() helper.
// Since Phase 5.1 the helper lives in electron/app/dashboard/format.js; extract
// it with a brace-balancing scan (its replace map contains {} so a naive regex
// fails), eval it, then assert that data-derived HTML is neutralised.
//   node --test tests/js/esc.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const FORMAT_JS = path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', 'format.js');

// Return the full `const esc = ...;` statement, tracking brace depth so the
// object literal inside .replace(...) does not terminate the scan early.
function extractEscStatement(src) {
  const start = src.indexOf('const esc =');
  assert.ok(start >= 0, 'esc() definition not found in format.js');
  let depth = 0;
  let i = start;
  for (; i < src.length; i++) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) break;
    }
  }
  assert.ok(i < src.length, 'unbalanced braces while extracting esc()');
  const semi = src.indexOf(';', i);
  assert.ok(semi > i, 'statement terminator not found for esc()');
  return src.slice(start, semi + 1);
}

const escStatement = extractEscStatement(fs.readFileSync(FORMAT_JS, 'utf8'));
// eslint-disable-next-line no-eval
const esc = eval(escStatement + '\nesc');

test('esc() neutralises dangerous tags', () => {
  for (const payload of ['<img src=x onerror=alert(1)>', '<script>alert(1)</script>', '"><svg onload=alert(1)>']) {
    const out = esc(payload);
    assert.ok(!/[<>"']/.test(out), `raw special characters survived: ${out}`);
    assert.ok(!out.includes('<img'), out);
    assert.ok(!out.includes('<script'), out);
    assert.ok(!out.includes('<svg'), out);
  }
});

test('esc() renders each special character as an entity', () => {
  const out = esc(`& < > " '`);
  assert.ok(out.includes('&lt;'), out);
  assert.ok(out.includes('&gt;'), out);
  assert.ok(out.includes('&quot;'), out);
  assert.ok(out.includes('&#39;'), out);
  assert.ok(out.includes('&amp;'), out);
  assert.ok(!/[<>"']/.test(out), out);
});

test('esc() leaves plain text unchanged', () => {
  assert.equal(esc('hello world 123 _-.'), 'hello world 123 _-.');
  assert.equal(esc(''), '');
  assert.equal(esc(null), '');
  assert.equal(esc(undefined), '');
});
