import { useState } from 'react';
import { Alert, Button, Input, Modal } from 'antd';
import { Trash2 } from 'lucide-react';
import { api } from '../services/api';

/** 删除涉及不可恢复的个人内容，需要用户明确输入确认；服务失败保留当前页面。 */
export function DeleteAction({
  path,
  label,
  explanation,
  disabled = false,
  onDeleted,
  onBusyChange,
}: {
  path: string;
  label: string;
  explanation: string;
  disabled?: boolean;
  onDeleted: () => void | Promise<void>;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const remove = async () => {
    if (confirmation !== '删除') return;
    setBusy(true);
    onBusyChange?.(true);
    setError('');
    try {
      await api(path, { method: 'DELETE' });
    } catch (error) {
      setError(error instanceof Error ? error.message : '删除失败，请重试');
      setBusy(false);
      onBusyChange?.(false);
      return;
    }
    setOpen(false);
    setBusy(false);
    onBusyChange?.(false);
    await onDeleted();
  };
  return (
    <>
      <Button
        type="text"
        danger
        aria-label={label}
        icon={<Trash2 size={15} aria-hidden="true" />}
        disabled={disabled || busy}
        onClick={() => {
          setConfirmation('');
          setError('');
          setOpen(true);
        }}
      >
        {label}
      </Button>
      <Modal
        title={label}
        open={open}
        onCancel={() => setOpen(false)}
        closable={!busy}
        mask={{ closable: !busy }}
        footer={[
          <Button
            key="cancel"
            aria-label="取消"
            disabled={busy}
            onClick={() => setOpen(false)}
          >
            取消
          </Button>,
          <Button
            key="confirm"
            danger
            type="primary"
            aria-label="确认删除"
            disabled={confirmation !== '删除' || busy}
            loading={busy}
            onClick={() => void remove()}
          >
            确认删除
          </Button>,
        ]}
      >
        <p>{explanation}</p>
        <p>此操作无法撤销。请输入「删除」确认。</p>
        <Input
          aria-label="输入删除以确认"
          value={confirmation}
          disabled={busy}
          onChange={(event) => setConfirmation(event.target.value)}
        />
        {error && <Alert type="error" showIcon title={error} role="alert" />}
      </Modal>
    </>
  );
}
