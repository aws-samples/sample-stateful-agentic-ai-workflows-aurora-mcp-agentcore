const { mkdtempSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');

// The AgentCore CDK library writes each Runtime's code zip to the OS temp folder on every synth
// and never deletes it. The test run gets one temp folder of its own, which teardown removes.
module.exports = () => {
  const folder = mkdtempSync(join(tmpdir(), 'meridian-cdk-test-'));
  process.env.MERIDIAN_TEST_TMPDIR = folder;
  process.env.TMPDIR = folder;
  process.env.TMP = folder;
  process.env.TEMP = folder;
};
