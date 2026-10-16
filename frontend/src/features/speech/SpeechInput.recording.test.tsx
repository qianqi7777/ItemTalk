import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { SpeechInput } from './SpeechInput';

const { upload, post, recordingToWav } = vi.hoisted(() => ({
  upload: vi.fn(),
  post: vi.fn(),
  recordingToWav: vi.fn(),
}));
vi.mock('../../services/api', () => ({ newKey: () => 'key', upload, post }));
vi.mock('../../utils/audio', () => ({ recordingToWav }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function pendingMicrophone() {
  let grant!: (stream: MediaStream) => void;
  let reject!: (error: Error) => void;
  const pending = new Promise<MediaStream>((resolve, fail) => {
    grant = resolve;
    reject = fail;
  });
  const getUserMedia = vi.fn(() => pending);
  const stop = vi.fn();
  const stream = { getTracks: () => [{ stop }] } as unknown as MediaStream;
  const recorder = {
    stream,
    start: vi.fn(),
    state: 'inactive',
    onstop: null as (() => Promise<void>) | null,
    mimeType: 'audio/webm',
  };
  const construct = vi.fn(function () {
    return recorder;
  });
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });
  vi.stubGlobal('MediaRecorder', construct);
  return { grant, reject, getUserMedia, stop, stream, recorder, construct };
}

test('离开页面后才授予麦克风权限时释放流，不启动录音或上传', async () => {
  const microphone = pendingMicrophone();
  const view = render(<SpeechInput onConfirm={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '录音提问' }));
  expect(microphone.getUserMedia).toHaveBeenCalledTimes(1);
  view.unmount();
  await act(async () => microphone.grant(microphone.stream));
  expect(microphone.stop).toHaveBeenCalledTimes(1);
  expect(microphone.construct).not.toHaveBeenCalled();
  expect(upload).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
});

test('等待麦克风授权时只申请一个流并禁用文件上传', async () => {
  const microphone = pendingMicrophone();
  render(<SpeechInput onConfirm={vi.fn()} />);
  const record = screen.getByRole('button', { name: '录音提问' });
  fireEvent.click(record);
  fireEvent.click(record);
  expect(record).toBeDisabled();
  expect(screen.getByLabelText('上传录音')).toBeDisabled();
  expect(microphone.getUserMedia).toHaveBeenCalledTimes(1);
  await act(async () => microphone.grant(microphone.stream));
  expect(microphone.construct).toHaveBeenCalledTimes(1);
  expect(microphone.recorder.start).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: '结束录音' })).toBeEnabled();
});

test('麦克风权限被拒绝后恢复上传入口且不调用转写', async () => {
  const microphone = pendingMicrophone();
  render(<SpeechInput onConfirm={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '录音提问' }));
  await act(async () => microphone.reject(new Error('Permission denied')));
  expect(
    screen.getByText('无法使用麦克风，请检查权限或上传录音文件'),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: '录音提问' })).toBeEnabled();
  expect(screen.getByLabelText('上传录音')).toBeEnabled();
  expect(microphone.construct).not.toHaveBeenCalled();
  expect(upload).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
});

test('上传完成前离开页面，不再请求付费转写', async () => {
  let finishUpload!: (asset: { id: string }) => void;
  upload.mockReturnValueOnce(
    new Promise((resolve) => {
      finishUpload = resolve;
    }),
  );
  const view = render(<SpeechInput onConfirm={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('上传录音'), {
    target: {
      files: [new File(['audio'], 'voice.wav', { type: 'audio/wav' })],
    },
  });
  expect(upload).toHaveBeenCalledTimes(1);
  view.unmount();
  await act(async () => finishUpload({ id: 'finished-audio' }));
  expect(post).not.toHaveBeenCalled();
});

test('录音解码完成前离开页面，不再上传或转写', async () => {
  const microphone = pendingMicrophone();
  let finishDecoding!: (file: File) => void;
  recordingToWav.mockReturnValueOnce(
    new Promise((resolve) => {
      finishDecoding = resolve;
    }),
  );
  const view = render(<SpeechInput onConfirm={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '录音提问' }));
  await act(async () => microphone.grant(microphone.stream));
  let stopped!: Promise<void>;
  act(() => {
    stopped = microphone.recorder.onstop!();
  });
  expect(recordingToWav).toHaveBeenCalledTimes(1);
  view.unmount();
  await act(async () => {
    finishDecoding(new File(['audio'], 'voice.wav', { type: 'audio/wav' }));
    await stopped;
  });
  expect(upload).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
});
