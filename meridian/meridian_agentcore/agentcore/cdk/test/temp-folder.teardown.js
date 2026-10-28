const { rmSync } = require('node:fs');

module.exports = () => {
  const folder = process.env.MERIDIAN_TEST_TMPDIR;
  if (folder) rmSync(folder, { recursive: true, force: true });
};
