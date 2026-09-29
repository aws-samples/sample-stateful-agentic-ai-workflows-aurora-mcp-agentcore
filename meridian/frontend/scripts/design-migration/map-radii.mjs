import { migrationCssFiles, transformCss, writeReport } from './lib.mjs';
import { mapRadius } from './radius.mjs';

const notes = [];
transformCss(migrationCssFiles(), (root, file) => {
  root.walkDecls(/^border(?:-[a-z]+)*-radius$/, decl => {
    const mapped = mapRadius(decl.value);
    if (mapped) decl.value = mapped;
    else notes.push(`- ${file}:${decl.source?.start?.line} REVIEW ${decl.prop}: ${decl.value}`);
  });
});
const report = writeReport('radii', ['# Radii', '', ...notes]);
console.log(`${notes.length} to review; report ${report}`);
