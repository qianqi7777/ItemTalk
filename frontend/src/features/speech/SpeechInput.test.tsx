import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { SpeechInput } from './SpeechInput';

vi.mock('../../services/api', () => ({
  newKey: () => 'key',
  upload: vi.fn(async () => ({ id: 'audio' })),
  post: vi.fn(async () => ({
    text: '识别的原始文字',
    requiresConfirmation: true,
  })),
}));
test('转写后等待用户编辑和确认，确认前不写入提问框', async () => {
  const confirm = vi.fn();
  render(<SpeechInput onConfirm={confirm} />);
  fireEvent.change(screen.getByLabelText('上传录音'), {
    target: {
      files: [new File(['audio'], 'voice.wav', { type: 'audio/wav' })],
    },
  });
  const draft = await screen.findByLabelText('转写文字');
  expect(confirm).not.toHaveBeenCalled();
  await userEvent.clear(draft);
  await userEvent.type(draft, '用户修改后的文字');
  await userEvent.click(screen.getByRole('button', { name: '确认文字' }));
  expect(confirm).toHaveBeenCalledWith('用户修改后的文字');
});
