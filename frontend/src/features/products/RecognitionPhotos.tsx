import { useEffect, useRef, useState } from 'react';
import { Button, Upload } from 'antd';
import { Camera, Upload as UploadIcon } from 'lucide-react';
import { upload } from '../../services/api';
import type { Asset } from '../../types/api';

export function RecognitionPhotos({
  photos,
  disabled,
  onUploaded,
  onRemove,
  onError,
  onUploading,
}: {
  photos: Asset[];
  disabled: boolean;
  onUploaded: (asset: Asset) => void;
  onRemove: (id: string) => void;
  onError: (message: string) => void;
  onUploading: (value: boolean) => void;
}) {
  const [pending, setPending] = useState(0);
  const pendingRef = useRef(0);
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  return (
    <div id="recognition-photos" className="recognition-photos">
      <Upload.Dragger
        accept="image/jpeg,image/png,image/webp"
        multiple
        showUploadList={false}
        disabled={disabled || pending > 0 || photos.length >= 10}
        beforeUpload={(file) => {
          if (file.size > 10 * 1024 * 1024) {
            onError('图片需小于 10 MB');
            return false;
          }
          if (photos.length + pendingRef.current >= 10) {
            onError('本次识别最多保留 10 张照片');
            return false;
          }
          pendingRef.current++;
          setPending(pendingRef.current);
          onUploading(true);
          void upload(file)
            .then((asset) => {
              if (active.current) onUploaded(asset);
            })
            .catch((failure) => {
              if (active.current)
                onError(
                  failure instanceof Error
                    ? failure.message
                    : '上传失败，请重试',
                );
            })
            .finally(() => {
              pendingRef.current--;
              if (active.current) {
                setPending(pendingRef.current);
                onUploading(pendingRef.current > 0);
              }
            });
          return false;
        }}
      >
        <div className="upload-visual">
          <Camera size={35} strokeWidth={1.5} aria-hidden="true" />
        </div>
        <h3>{photos.length ? '照片已上传' : '把你的设备，放进这里'}</h3>
        <p>
          {photos.length
            ? '继续添加正面、背面或局部照片，原照片会保留'
            : '先拍完整外观，不需要先找到铭牌'}
        </p>
        <span className="upload-prompt">
          <UploadIcon size={15} aria-hidden="true" />
          {photos.length ? '继续添加照片' : '选择照片'}
        </span>
        <small>JPG、PNG、WebP · 每张最大 10 MB · 累计最多 10 张</small>
      </Upload.Dragger>
      {!!photos.length && (
        <>
          <p>已保留 {photos.length} / 10 张照片，将综合这些角度判断。</p>
          <ul className="recognition-photo-grid" aria-label="本轮产品照片">
            {photos.map((photo, index) => (
              <li key={photo.id}>
                {photo.readUrl && (
                  <img src={photo.readUrl} alt={`产品照片 ${index + 1}`} />
                )}
                <Button
                  size="small"
                  disabled={disabled || pending > 0}
                  aria-label={`从本轮移除照片 ${index + 1}`}
                  onClick={() => onRemove(photo.id)}
                >
                  移除
                </Button>
              </li>
            ))}
          </ul>
          <small>
            未提交照片移除后会删除上传；已提交照片只从接下来的判断中移除，历史记录仍保留。
          </small>
        </>
      )}
    </div>
  );
}
