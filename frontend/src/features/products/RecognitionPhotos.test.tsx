import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { RecognitionPhotos } from './RecognitionPhotos';
import { ConfigProvider } from 'antd';

test('keeps multiple photos visible and removes only the chosen photo', () => {
  const remove = vi.fn();
  render(
    <RecognitionPhotos
      photos={[
        {
          id: 'back',
          readUrl: '/back.jpg',
          mimeType: 'image/jpeg',
          sizeBytes: 30,
          status: 'available',
          kind: 'product_photo',
        },
        {
          id: 'front',
          readUrl: '/front.jpg',
          mimeType: 'image/jpeg',
          sizeBytes: 30,
          status: 'available',
          kind: 'product_photo',
        },
      ]}
      disabled={false}
      onUploaded={vi.fn()}
      onRemove={remove}
      onError={vi.fn()}
      onUploading={vi.fn()}
    />,
  );
  expect(screen.getAllByRole('img', { name: /产品照片/ })).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: '从本轮移除照片 1' }));
  expect(remove).toHaveBeenCalledWith('back');
  expect(screen.getByRole('img', { name: '产品照片 2' })).toHaveAttribute(
    'src',
    '/front.jpg',
  );
});

test('紧凑照片条仍能展开核对原图', async () => {
  const remove = vi.fn();
  render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <RecognitionPhotos
        compact
        collapsed
        photos={[
          {
            id: 'photo',
            readUrl: '/private-photo.jpg',
            mimeType: 'image/jpeg',
            sizeBytes: 30,
            status: 'available',
            kind: 'product_photo',
          },
        ]}
        disabled={false}
        onUploaded={vi.fn()}
        onRemove={remove}
        onError={vi.fn()}
        onUploading={vi.fn()}
      />
    </ConfigProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: '查看产品照片 1' }));
  await waitFor(() =>
    expect(screen.getByRole('dialog', { name: '核对产品照片' })).toBeVisible(),
  );
  expect(screen.getByRole('img', { name: '产品照片原图' })).toHaveAttribute(
    'src',
    '/private-photo.jpg',
  );
  fireEvent.click(screen.getByRole('button', { name: '从本轮移除这张照片' }));
  expect(remove).toHaveBeenCalledWith('photo');
});
