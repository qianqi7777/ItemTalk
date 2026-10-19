import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppShell } from './AppShell';

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
});

function shell(admin = true, route = '/admin', logout = vi.fn(async () => {})) {
  render(
    <MemoryRouter initialEntries={[route]}>
      <AppShell
        user={{ id: 'test-account', email: 'reader@example.com' }}
        admin={admin}
        onRefresh={async () => {}}
        onLogout={logout}
      >
        <h1>页面内容</h1>
      </AppShell>
    </MemoryRouter>,
  );
  return logout;
}

describe('responsive application navigation', () => {
  it('has one route entry per desktop destination and marks the actual route', () => {
    shell();
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0);
    expect(document.activeElement).toHaveAttribute('id', 'main-content');
    const navigation = within(
      screen.getByRole('navigation', { name: '主导航' }),
    );
    expect(
      navigation.getAllByRole('link', { name: '设备工作区' }),
    ).toHaveLength(1);
    expect(navigation.getByRole('link', { name: '运行管理' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(
      navigation.getByRole('link', { name: '设备工作区' }),
    ).not.toHaveAttribute('aria-current');
    expect(
      within(
        screen.getByRole('navigation', { name: '移动页面导航' }),
      ).getByRole('link', { name: '运行管理' }),
    ).toHaveAttribute('aria-current', 'page');
  });

  it('does not offer administrator navigation to an ordinary account', () => {
    shell(false, '/');
    expect(
      screen.queryByRole('link', { name: '运行管理' }),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('navigation', { name: '主导航' })).getByRole(
        'link',
        { name: '设备工作区' },
      ),
    ).toHaveAttribute('aria-current', 'page');
  });

  it('places destructive account actions in an explicitly opened drawer', async () => {
    const user = userEvent.setup();
    const logout = shell();
    expect(
      screen.queryByRole('button', { name: '删除账号' }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '打开账号菜单' }));
    const drawer = within(
      await screen.findByRole('dialog', { name: '账号与隐私' }),
    );
    expect(drawer.getByRole('button', { name: '删除账号' })).toBeVisible();
    await user.click(drawer.getByRole('button', { name: '退出登录' }));
    expect(logout).toHaveBeenCalledOnce();
  });

  it('keeps the account drawer available when logout fails', async () => {
    const user = userEvent.setup();
    shell(
      true,
      '/',
      vi.fn(async () => {
        throw new Error('offline');
      }),
    );
    await user.click(screen.getByRole('button', { name: '打开账号菜单' }));
    await user.click(await screen.findByRole('button', { name: '退出登录' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '退出失败，请重试。',
    );
    expect(screen.getByRole('dialog', { name: '账号与隐私' })).toBeVisible();
  });
});
