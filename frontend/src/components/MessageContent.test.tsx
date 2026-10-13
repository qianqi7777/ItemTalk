import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { MessageContent } from './MessageContent';
import type { Citation } from '../types/api';

test('模型回答的粗体、列表与代码以可读的正文格式展示', () => {
  const { container } = render(
    <MessageContent
      content={
        '## 使用方式\n\n1. **连接电源**\n2. 检查 `USB-C`\n\n- 完成后断开连接'
      }
    />,
  );
  expect(screen.getByRole('heading', { name: '使用方式' })).toBeVisible();
  expect(container.querySelector('strong')).toHaveTextContent('连接电源');
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
  expect(container.querySelector('code')).toHaveTextContent('USB-C');
});

test('不执行原始 HTML 或模型生成的链接，代码段保持原文', () => {
  const content =
    '<script>alert(1)</script>\n\n[危险链接](javascript:alert(1))\n\n```html\n<img src=x onerror=alert(1)>\n```';
  const { container } = render(<MessageContent content={content} />);
  expect(container.querySelector('script')).toBeNull();
  expect(container.querySelector('img')).toBeNull();
  expect(container.querySelector('a')).toBeNull();
  expect(container.querySelector('pre')).toHaveTextContent(
    '<img src=x onerror=alert(1)>',
  );
});

test('只为服务端已提供的引用编号增加标记，未知编号保留原文', () => {
  const citation = { rank: 1 } as Citation;
  const { container } = render(
    <MessageContent
      content="官方说明[1]，未知来源[9]"
      citations={[citation]}
    />,
  );
  expect(container.querySelectorAll('sup')).toHaveLength(1);
  expect(container.querySelector('sup')).toHaveTextContent('1');
  expect(container).toHaveTextContent('未知来源[9]');
});
