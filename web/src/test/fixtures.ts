// Test helper: a mock adapter that reads fixtures/mock_api/ straight from disk.
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createMockApi, type MockSettings } from '../api/mock';

// vitest runs from web/
export const FIXTURES = resolve(process.cwd(), '../fixtures/mock_api');

export function diskMock(settings: Partial<MockSettings> = {}) {
  return createMockApi((file) => readFile(resolve(FIXTURES, file), 'utf8'), settings);
}
