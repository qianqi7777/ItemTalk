import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Spin } from 'antd';
import { Mic, Square, Upload } from 'lucide-react';
import { newKey, post, upload } from '../../services/api';
import { recordingToWav } from '../../utils/audio';

/** 转写是可编辑草稿；只有用户点击确认后才进入提问输入框，不自动发起问答。 */
export function SpeechInput({
  onConfirm,
}: {
  onConfirm: (text: string) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState('');
  const recorder = useRef<MediaRecorder | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current) clearTimeout(timer.current);
      recorder.current?.stream.getTracks().forEach((track) => track.stop());
    };
  }, []);
  const transcribe = async (file: File) => {
    setBusy(true);
    setError('');
    try {
      if (file.size > 7 * 1024 * 1024) throw new Error('转写音频需小于 7 MB');
      const asset = await upload(file);
      const result = await post<{
        text: string;
        requiresConfirmation: boolean;
      }>(`/assets/${asset.id}/transcribe`, {}, newKey());
      if (mounted.current) setDraft(result.text);
    } catch (error) {
      if (mounted.current)
        setError(error instanceof Error ? error.message : '语音转写失败');
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  const stop = () => {
    if (timer.current) clearTimeout(timer.current);
    if (recorder.current?.state === 'recording') recorder.current.stop();
  };
  const start = async () => {
    setError('');
    let stream: MediaStream | undefined;
    try {
      if (
        !navigator.mediaDevices?.getUserMedia ||
        typeof MediaRecorder === 'undefined'
      )
        throw new Error('Recording unavailable');
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const media = new MediaRecorder(stream);
      recorder.current = media;
      const chunks: Blob[] = [];
      media.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      media.onstop = async () => {
        media.stream.getTracks().forEach((track) => track.stop());
        if (!mounted.current) return;
        setRecording(false);
        setBusy(true);
        try {
          await transcribe(
            await recordingToWav(new Blob(chunks, { type: media.mimeType })),
          );
        } catch (error) {
          setError(error instanceof Error ? error.message : '录音格式转换失败');
          setBusy(false);
        }
      };
      media.start();
      setRecording(true);
      timer.current = setTimeout(stop, 60000);
    } catch {
      stream?.getTracks().forEach((track) => track.stop());
      setError('无法使用麦克风，请检查权限或上传录音文件');
    }
  };
  return (
    <section className="speech-input" aria-label="语音转写" aria-live="polite">
      <div className="inline-actions">
        {recording ? (
          <Button onClick={stop} icon={<Square size={16} />}>
            结束录音
          </Button>
        ) : (
          <Button
            onClick={() => void start()}
            disabled={busy}
            icon={<Mic size={16} />}
          >
            录音提问
          </Button>
        )}
        <label className="audio-upload-label">
          <Upload size={15} aria-hidden="true" />
          <span>上传录音</span>
          <input
            aria-label="上传录音"
            type="file"
            accept="audio/wav,audio/mpeg,audio/mp4,audio/webm"
            disabled={busy || recording}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void transcribe(file);
              event.target.value = '';
            }}
          />
        </label>
      </div>
      {recording && <small>正在录音，最长 60 秒；结束后先核对转写文字。</small>}
      {busy && (
        <div>
          <Spin size="small" /> 正在转写录音…
        </div>
      )}
      {error && <Alert title={error} type="warning" showIcon />}
      {draft !== null && (
        <div className="transcription-review">
          <p>请核对并修改识别文字，确认后放入提问框。</p>
          <Input.TextArea
            aria-label="转写文字"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={10000}
          />
          <div className="inline-actions">
            <Button
              type="primary"
              disabled={!draft.trim()}
              onClick={() => {
                onConfirm(draft.trim());
                setDraft(null);
              }}
            >
              确认文字
            </Button>
            <Button onClick={() => setDraft(null)}>丢弃</Button>
          </div>
        </div>
      )}
      <small>录音会发送至已配置的语音服务进行转写。</small>
    </section>
  );
}
