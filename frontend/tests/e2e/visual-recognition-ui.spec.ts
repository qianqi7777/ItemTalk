import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const back = fs.readFileSync(
  path.join(import.meta.dirname, '../fixtures/recognition-back.png'),
);
const front = fs.readFileSync(
  path.join(import.meta.dirname, '../fixtures/recognition-front.png'),
);
const ids = [
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
];
for (const width of [320, 390, 1440]) {
  test(`外观候选、多轮补拍、刷新恢复与用户选择 ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const photos: {
      id: string;
      mimeType: string;
      sizeBytes: number;
      status: string;
      kind: string;
      readUrl: string;
    }[] = [];
    let submitted = 0;
    let selected = '';
    const candidate = (code: string, id: string) => ({
      candidateId: id,
      basis: 'appearance',
      brandName: 'Acme',
      modelCode: code,
      confidence: 0.6,
      evidence: '镜头排列相似',
      matchReasons: ['两个镜头排列相似'],
      differences: ['正面边框仍需比较'],
      missingEvidence: ['需要正面照片'],
      referenceImage: 'data:image/png;base64,' + back.toString('base64'),
      sources: [
        {
          id: 's-' + code,
          title: code + ' 产品资料',
          url: 'https://vendor.example/' + code.replace(' ', '-'),
          sourceKind: 'official',
        },
      ],
    });
    const candidates = [
      candidate('Phone 16', '33333333-3333-4333-8333-333333333333'),
      candidate('Phone 17', '44444444-4444-4444-8444-444444444444'),
      candidate('Phone 18', '55555555-5555-4555-8555-555555555555'),
    ];
    const result = (index: number, status = 'waiting_for_user') => ({
      id: ids[index],
      status,
      jobId: `job-${index}`,
      roundNo: index + 1,
      maxRounds: 4,
      candidates:
        status === 'queued'
          ? []
          : index
            ? [candidates[2]]
            : candidates.slice(0, 2).map((value) => ({
                ...value,
                evidenceLevel: 'visual_guess',
                sources: [],
                referenceImage: null,
              })),
      photos: photos.slice(0, index + 1),
      clarification: '候选来自实际产品资料，外观仍需由你确认。',
      observation:
        status === 'queued'
          ? null
          : {
              category: '智能手机',
              brandHint: 'Acme',
              features: ['背面有两枚镜头', '机身边框平直'],
            },
      photoRequests: [
        {
          target: 'front',
          reason: '通过屏幕和边框进一步区分候选',
          instruction: '补拍完整正面和屏幕边框',
        },
      ],
      researchStatus: 'found',
    });
    await page.route('**/api/v1/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const pathname = url.pathname.replace(/^.*\/api\/v1/, '');
      const reply = (json: unknown, status = 200) =>
        route.fulfill({ status, json });
      if (pathname === '/me')
        return reply({
          id: 'visual-user',
          email: 'preview@example.com',
          displayName: '合成验收',
        });
      if (pathname === '/capabilities')
        return reply({ speechToText: false, adminConsole: false });
      if (pathname === '/me/products') return reply({ items: [] });
      if (pathname === '/assets/presign') {
        const id = 'photo-' + (photos.length + 1);
        return reply(
          {
            assetId: id,
            uploadUrl: `${url.origin}/api/v1/assets/${id}/content`,
          },
          201,
        );
      }
      if (pathname.endsWith('/content') && request.method() === 'PUT')
        return route.fulfill({ status: 204 });
      if (pathname.startsWith('/assets/') && pathname.endsWith('/confirm')) {
        const id = pathname.split('/')[2];
        const photo = {
          id,
          mimeType: 'image/png',
          sizeBytes: 1200,
          status: 'available',
          kind: 'product_photo',
          readUrl:
            'data:image/png;base64,' +
            (photos.length ? front : back).toString('base64'),
        };
        photos.push(photo);
        return reply(photo);
      }
      if (pathname === '/products/identify') {
        const input = request.postDataJSON();
        expect(input.assetIds).toEqual(
          submitted ? ['photo-1', 'photo-2'] : ['photo-1'],
        );
        if (submitted) expect(input.supersedesAttemptId).toBe(ids[0]);
        if (submitted)
          expect(input.rejectedCandidateIds).toEqual(
            candidates.slice(0, 2).map((value) => value.candidateId),
          );
        submitted++;
        return reply(result(submitted - 1, 'queued'), 202);
      }
      if (
        pathname.startsWith('/identifications/') &&
        pathname.endsWith('/confirm')
      ) {
        selected = request.postDataJSON().candidateId;
        expect(selected).toBe(candidates[2].candidateId);
        return reply(
          {
            id: 'device',
            productModelId: 'model',
            nickname: null,
            modelCode: 'Phone 18',
            brandName: 'Acme',
            identityStatus: 'user_confirmed',
            verificationStatus: 'pending',
            knowledgeStatus: 'ready',
            knowledgeJobId: null,
          },
          201,
        );
      }
      if (pathname.startsWith('/identifications/'))
        return reply(result(pathname.endsWith(ids[0]) ? 0 : 1));
      if (pathname === '/conversations')
        return reply(
          { id: 'conversation', userProductInstanceId: 'device' },
          201,
        );
      if (pathname.endsWith('/messages'))
        return reply({ items: [], nextBefore: null });
      return reply({}, 404);
    });
    await page.goto('/');
    await page
      .locator('#recognition-photos input[type=file]')
      .setInputFiles({ name: 'back.png', mimeType: 'image/png', buffer: back });
    await expect(
      page.getByRole('img', { name: '产品照片 1', exact: true }),
    ).toBeVisible();
    await page.getByRole('button', { name: '开始识别', exact: true }).click();
    await expect(
      page.getByText('先看看它是什么', { exact: true }),
    ).toBeVisible();
    await expect(page.locator('.candidate')).toHaveCount(2);
    await expect(
      page.getByText('外观猜测 · 资料尚未核实', { exact: true }),
    ).toHaveCount(2);
    await expect(
      page.getByRole('button', { name: '确认此型号', exact: true }),
    ).toBeDisabled();
    await page
      .getByRole('radio', { name: '选择 Acme Phone 17', exact: true })
      .check();
    await expect(
      page.getByRole('button', { name: '确认此型号', exact: true }),
    ).toBeEnabled();
    await page
      .getByRole('button', { name: '都不是，排除这些候选', exact: true })
      .click();
    expect(submitted).toBe(1);
    await expect(
      page.getByText('已排除这些候选，请补充文字或照片，再继续判断。', {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: '综合照片继续判断', exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByText('补拍完整正面和屏幕边框', { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/60%/)).toHaveCount(0);
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: '继续补拍', exact: true }).click();
    await (
      await chooser
    ).setFiles({ name: 'front.png', mimeType: 'image/png', buffer: front });
    await expect(
      page.getByRole('img', { name: '产品照片 2', exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: '综合照片继续判断', exact: true })
      .click();
    await expect(page.locator('.candidate')).toHaveCount(1);
    await expect(page.getByText('第 2 / 4 轮', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.locator('.candidate')).toHaveCount(1);
    await expect(
      page.getByRole('img', { name: '产品照片 2', exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.locator('.identify-card').screenshot({
      path: `test-results/visual-recognition/visual-recognition-${width}.png`,
      animations: 'disabled',
      style: '.topbar,.mobile-nav,.skip-link { visibility:hidden; }',
    });
    await page.getByRole('button', { name: '确认此型号', exact: true }).click();
    await expect(page.getByText('资料已就绪', { exact: true })).toBeVisible();
    expect(selected).toBe(candidates[2].candidateId);
  });
}
