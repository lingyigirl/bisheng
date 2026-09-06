module.exports = {
  roots: ['<rootDir>/src'],
  // Custom environment: disables the canvas native binding (missing on dev
  // machines) before jsdom loads — see test/jsdomEnvironment.js.
  testEnvironment: '<rootDir>/test/jsdomEnvironment.js',
  testEnvironmentOptions: {
    url: 'http://localhost:3080',
  },
  collectCoverage: true,
  collectCoverageFrom: [
    'src/**/*.{js,jsx,ts,tsx}',
    '!<rootDir>/node_modules/',
    '!src/**/*.css.d.ts',
    '!src/**/*.d.ts',
  ],
  coveragePathIgnorePatterns: ['<rootDir>/node_modules/', '<rootDir>/test/setupTests.js'],
  //  Todo: Add coverageThreshold once we have enough coverage
  //  Note: eventually we want to have these values set to 80%
  // coverageThreshold: {
  //   global: {
  //     functions: 9,
  //     lines: 40,
  //     statements: 40,
  //     branches: 12,
  //   },
  // },
  moduleNameMapper: {
    // jsdom's optional `canvas` dependency has no native binding in this
    // environment — route it to jest-canvas-mock (already used in setupTests).
    '^canvas$': 'jest-canvas-mock',
    '\\.(css)$': 'identity-obj-proxy',
    '\\.(jpg|jpeg|png|gif|eot|otf|webp|svg|ttf|woff|woff2|mp4|webm|wav|mp3|m4a|aac|oga)$':
      'jest-file-loader',
    '^test/(.*)$': '<rootDir>/test/$1',
    '^~/(.*)$': '<rootDir>/src/$1',
    '^~/data-provider/data-provider/src/react-query$': '<rootDir>/../node_modules/~/data-provider/data-provider/src/src/react-query',
  },
  restoreMocks: true,
  testResultsProcessor: 'jest-junit',
  coverageReporters: ['text', 'cobertura', 'lcov'],
  transform: {
    '\\.[jt]sx?$': 'babel-jest',
    '\\.(jpg|jpeg|png|gif|eot|otf|webp|svg|ttf|woff|woff2|mp4|webm|wav|mp3|m4a|aac|oga)$':
      'jest-file-loader',
  },
  // @bisheng/ui is source-shipped TS from the workspace — jest must transform it
  // (default ignore would skip everything under node_modules).
  transformIgnorePatterns: ['node_modules/(?!(@bisheng/ui|@zattoo/use-double-click)/)'],
  preset: 'ts-jest',
  setupFilesAfterEnv: ['@testing-library/jest-dom/extend-expect', '<rootDir>/test/setupTests.js'],
  clearMocks: true,
};
