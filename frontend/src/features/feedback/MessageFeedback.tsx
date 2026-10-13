import { useState } from 'react';
import { Alert, Button, Input } from 'antd';
import { ThumbsDown, ThumbsUp } from 'lucide-react';
import { post } from '../../services/api';
import type { Message } from '../../types/api';

export function MessageFeedback({ message }: { message: Message }) {
  const [feedback, setFeedback] = useState(message.feedback);
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState(message.feedback?.note || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const submit = async (feedbackType: 'helpful' | 'incorrect') => {
    setSaving(true);
    setError('');
    try {
      const value = {
        feedbackType,
        note: feedbackType === 'incorrect' ? note.trim() || null : null,
      };
      await post(`/messages/${message.id}/feedback`, value);
      setFeedback(value);
      setEditing(false);
    } catch (error) {
      setError(error instanceof Error ? error.message : '反馈提交失败');
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="message-feedback">
      <div className="inline-actions">
        <Button
          size="small"
          icon={<ThumbsUp size={13} aria-hidden="true" />}
          disabled={saving}
          aria-pressed={feedback?.feedbackType === 'helpful'}
          onClick={() => void submit('helpful')}
        >
          有帮助
        </Button>
        <Button
          size="small"
          icon={<ThumbsDown size={13} aria-hidden="true" />}
          disabled={saving}
          aria-pressed={feedback?.feedbackType === 'incorrect'}
          onClick={() => setEditing(!editing)}
        >
          需要纠正
        </Button>
        {feedback && <small role="status">反馈已记录</small>}
      </div>
      {editing && (
        <div className="feedback-editor">
          <Input.TextArea
            aria-label="纠正说明"
            placeholder="可选：哪里需要纠正？请勿填写密码或联系方式。"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={2000}
            autoSize={{ minRows: 2 }}
          />
          <Button
            size="small"
            loading={saving}
            onClick={() => void submit('incorrect')}
          >
            提交纠正反馈
          </Button>
        </div>
      )}
      {error && <Alert type="error" title={error} showIcon />}
    </div>
  );
}
