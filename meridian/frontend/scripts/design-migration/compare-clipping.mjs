import fs from 'node:fs';
import path from 'node:path';

const [baseDir, nextDir] = process.argv.slice(2).map(dir => path.resolve(dir));
const read = (dir, file) => {
  const full = path.join(dir, file);
  return fs.existsSync(full) ? JSON.parse(fs.readFileSync(full, 'utf8')) : [];
};

let added = 0;
for (const file of fs.readdirSync(nextDir).filter(name => name.startsWith('clipped-')).sort()) {
  const before = new Set(read(baseDir, file));
  const fresh = read(nextDir, file).filter(entry => !before.has(entry));
  if (fresh.length === 0) continue;
  console.log(`\n${file}`);
  for (const entry of fresh) console.log(`  ${entry}`);
  added += fresh.length;
}
console.log(`\n${added} newly clipped elements`);
