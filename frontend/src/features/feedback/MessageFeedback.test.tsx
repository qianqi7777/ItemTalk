import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { post } from '../../services/api';
import { MessageFeedback } from './MessageFeedback';

vi.mock('../../services/api', () => ({ post: vi.fn(async () => undefined) }));

test('反馈只在用户提交后发送，并可改成带说明的纠正反馈', async () => {
  render(
    <MessageFeedback
      message={{
        id: 'answer',
        role: 'assistant',
        content: '答案',
        citations: [],
      }}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: '有帮助' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: '有帮助' })).toHaveAttribute(
      'aria-pressed',
      'true',
    ),
  );
  expect(post).toHaveBeenLastCalledWith('/messages/answer/feedback', {
    feedbackType: 'helpful',
    note: null,
  });
  await userEvent.click(screen.getByRole('button', { name: '需要纠正' }));
  expect(post).toHaveBeenCalledTimes(1);
  await userEvent.type(screen.getByLabelText('纠正说明'), '按键位置不同');
  await userEvent.click(screen.getByRole('button', { name: '提交纠正反馈' }));
  await waitFor(() =>
    expect(post).toHaveBeenLastCalledWith('/messages/answer/feedback', {
      feedbackType: 'incorrect',
      note: '按键位置不同',
    }),
  );
  expect(screen.getByRole('button', { name: '需要纠正' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});

test('提交失败时保留纠正草稿且不显示已保存状态', async () => {
  vi.mocked(post).mockRejectedValueOnce(new Error('反馈服务暂时不可用'));
  render(
    <MessageFeedback
      message={{
        id: 'retry',
        role: 'assistant',
        content: '答案',
        citations: [],
      }}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: '需要纠正' }));
  await userEvent.type(screen.getByLabelText('纠正说明'), '重试说明');
  await userEvent.click(screen.getByRole('button', { name: '提交纠正反馈' }));
  expect(await screen.findByText('反馈服务暂时不可用')).toBeVisible();
  expect(screen.getByLabelText('纠正说明')).toHaveValue('重试说明');
  expect(screen.queryByText('反馈已记录')).not.toBeInTheDocument();
});
