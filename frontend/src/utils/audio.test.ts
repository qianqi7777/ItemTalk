import { expect, test } from 'vitest';
import { encodeWav } from './audio';

test('录音转为 16kHz 单声道 PCM WAV，限制采样幅度', () => {
  const buffer = encodeWav(new Float32Array([-2, 0, 2]));
  const view = new DataView(buffer);
  expect(view.getUint32(24, true)).toBe(16000);
  expect(view.getUint16(22, true)).toBe(1);
  expect(view.getUint32(40, true)).toBe(6);
  expect(view.getInt16(44, true)).toBe(-32768);
  expect(view.getInt16(48, true)).toBe(32767);
});
