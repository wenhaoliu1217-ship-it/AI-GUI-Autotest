import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const plan = {
  name: '管理员登录验收',
  base_url: 'http://127.0.0.1:8765',
  role: '测试工程师',
  preconditions: [],
  steps: [
    { action: 'navigate', target: '/', description: '打开目标网站' },
    { action: 'click', locator: { role: 'button', name: '登录' }, description: '点击登录' }
  ],
  assertions: [{ type: 'visible', locator: { text: '客户管理' }, description: '确认客户管理可见' }]
};

const backendRun = {
  run_id: '20260719-120000-abcd1234',
  plan_name: plan.name,
  role: plan.role,
  base_url_summary: plan.base_url,
  status: 'passed',
  started_at: '2026-07-19T12:00:00+08:00',
  ended_at: '2026-07-19T12:00:01+08:00',
  steps: [{
    index: 1,
    action: 'navigate',
    description: '打开目标网站',
    target_summary: '打开目标网站 -> /',
    status: 'passed',
    started_at: '2026-07-19T12:00:00+08:00',
    ended_at: '2026-07-19T12:00:01+08:00',
    screenshot: 'screenshots/step-1-passed.png'
  }],
  assertions: [{ index: 1, type: 'visible', description: '确认客户管理可见', status: 'passed', actual_summary: 'visible' }],
  reproduction_steps: ['打开目标网站 -> /'],
  cause_hints: [],
  artifact_base_url: '/api/artifacts/20260719-120000-abcd1234'
};

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
}

beforeEach(() => {
  vi.stubGlobal('open', vi.fn());
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/api/health')) return jsonResponse({ status: 'ok', mode: 'real', engine: 'playwright-chromium', planner: 'deterministic-rules' });
    if (url.endsWith('/api/ai/test')) return jsonResponse({ connected: true, model: 'gpt-5.6-terra', protocol: 'responses', elapsedMs: 123 });
    if (url.endsWith('/api/ai/plans/generate')) return jsonResponse({ plan, warnings: [], planner: 'ai:responses:gpt-5.6-terra' });
    if (url.endsWith('/api/plans/generate')) return jsonResponse({ plan, warnings: [], planner: 'deterministic-rules' });
    if (url.endsWith('/api/plans/validate')) return jsonResponse({ valid: true, plan });
    if (url.endsWith('/api/runs') && init?.method === 'POST') return jsonResponse(backendRun);
    if (url.endsWith('/api/runs')) return jsonResponse([]);
    if (url.includes('/api/runs/')) return jsonResponse(backendRun);
    return jsonResponse({ detail: 'not found' }, 404);
  }));
});

describe('App', () => {
  it('shows real execution mode without fabricated history', async () => {
    render(<App />);
    expect(screen.getByText('真实运行记录')).toBeInTheDocument();
    expect(await screen.findByText('真实执行服务已连接')).toBeInTheDocument();
    expect(screen.getByText('尚无真实运行记录。Mock 样例已移除。')).toBeInTheDocument();
    expect(screen.queryByText('制造一个失败结果')).not.toBeInTheDocument();
  });

  it('requires a reviewed backend plan before running', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getAllByRole('button', { name: /新建测试/ })[0]);
    expect(screen.getByRole('button', { name: /启动真实浏览器测试/ })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    expect(await screen.findByText('可审核执行计划')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /审核并校验计划/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /启动真实浏览器测试/ })).toBeEnabled());
  });

  it('configures AI in memory and enables AI planning after a real connection test', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: /AI 模型设置/ }));
    const keyInput = screen.getByLabelText('API Key');
    expect(keyInput).toHaveAttribute('type', 'password');
    await user.type(keyInput, 'new-test-key');
    await user.click(screen.getByRole('button', { name: /测试模型连接/ }));
    expect(await screen.findByText(/连接成功：gpt-5.6-terra/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /使用 AI 新建测试/ })).toBeEnabled();
    const calls = vi.mocked(fetch).mock.calls;
    const request = calls.find(([url]) => String(url).endsWith('/api/ai/test'));
    expect(request?.[1]?.body).toContain('new-test-key');
    expect(document.body.textContent).not.toContain('new-test-key');
  });

  it('opens a real screenshot artifact from a completed run', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getAllByRole('button', { name: /新建测试/ })[0]);
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await screen.findByText('可审核执行计划');
    await user.click(screen.getByRole('button', { name: /审核并校验计划/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /启动真实浏览器测试/ })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));
    const screenshot = await screen.findByRole('button', { name: '截图' });
    expect(screenshot).toBeEnabled();
    await user.click(screenshot);
    expect(window.open).toHaveBeenCalledWith(expect.stringContaining('/api/artifacts/'), '_blank', 'noopener,noreferrer');
  });
});
