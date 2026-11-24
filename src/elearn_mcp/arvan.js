// Solve ArvanCloud __arcsjs challenge: extract values() + XOR helper, compute cookies.
// Reads the challenge HTML from stdin, prints {"arcsjs": ..., "arcsjsc": ...}.
const fs = require('fs');
const html = fs.readFileSync(0, 'utf8');
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const s = scripts.find(x => x.includes('hash_v1'));
if (!s) {
  console.error('no script containing hash_v1 found');
  process.exit(1);
}
const code = s.split('document.addEventListener')[0];
const vm = require('vm');
const ctx = {};
vm.createContext(ctx);
vm.runInContext(code.replace(/var _this = this;/, ''), ctx);
console.log(JSON.stringify({ arcsjs: ctx.hash_v1, arcsjsc: ctx.hash }));
