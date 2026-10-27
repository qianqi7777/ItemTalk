import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { RecognitionPhotos } from './RecognitionPhotos';

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
