module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  transform: {
    '^.+\\.tsx?$': 'ts-jest',
  },
  setupFilesAfterEnv: ['aws-cdk-lib/testhelpers/jest-autoclean'],
  globalSetup: '<rootDir>/test/temp-folder.setup.js',
  globalTeardown: '<rootDir>/test/temp-folder.teardown.js',
};
