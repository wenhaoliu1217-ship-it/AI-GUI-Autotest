from __future__ import annotations
import argparse, functools, hashlib, http.server, json, os, pathlib, subprocess, sys, threading
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))
class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_): pass

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host-browser',action='store_true')
    parser.add_argument('--fixture-host',default='host.docker.internal')
    args=parser.parse_args()
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    os.environ['GUI_RUNNER_IMAGE']='ai-gui-runner:1.40.01'
    os.environ['GUI_DELIVERY_ID']=hashlib.sha256(str(ROOT).lower().encode()).hexdigest()
    if sys.platform=='win32': os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'runtime/ms-playwright')
    server=http.server.ThreadingHTTPServer(('0.0.0.0',0),functools.partial(QuietHandler,directory=str(ROOT/'acceptance/fixture')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    out=ROOT/'.acceptance-evidence';out.mkdir(exist_ok=True)
    try:
        if args.host_browser:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                page=browser.new_page()
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.get_by_label('测试名称').fill('企业验收')
                page.get_by_role('button',name='执行示例').click()
                assert page.locator('#result').inner_text()=='已完成：企业验收'
                page.screenshot(path=str(out/'host-browser.png'))
                browser.close()
            result={'mode':'host_browser_diagnostic','passed':True,'note':'Bundled browser only; does not certify isolated Runner.'}
        else:
            from gui_agent.domain.models import TestPlan
            from gui_agent.execution.runner import RunnerConfig
            from gui_agent.execution.orchestrator import RunOrchestrator
            plan=TestPlan.model_validate({'name':'企业验收本地示例','base_url':f'http://{args.fixture_host}:{server.server_port}',
              'steps':[{'action':'navigate','target':'/','description':'打开示例'},
                       {'action':'fill','locator':{'label':'测试名称'},'value':'企业验收','description':'填写示例名称'},
                       {'action':'click','locator':{'role':'button','name':'执行示例'},'description':'执行示例'}],
              'assertions':[{'type':'text_contains','locator':{'css':'#result'},'expected':'已完成：企业验收','description':'校验真实页面结果'}]})
            cfg=RunnerConfig(artifacts_root=out,headless=True,allow_private_network=True,allowed_hosts=(args.fixture_host,),timeout_ms=15000,max_duration_seconds=120)
            result=RunOrchestrator(runner_mode='container').run_blocking(plan,cfg)
            run_dir=out/result['run_id']
            assert result['status']=='passed',json.dumps(result,ensure_ascii=False)
            assert result.get('goal_status')=='achieved',result.get('completion_gate')
            assert (run_dir/'report.html').is_file(),'HTML report missing'
            assert (run_dir/'trace.zip').is_file(),'Trace missing'
            assert list((run_dir/'screenshots').glob('*.png')),'Screenshots missing'
        (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':True,'runId':result.get('run_id'),'mode':result.get('mode','docker_container')},ensure_ascii=False))
    finally: server.shutdown()
if __name__=='__main__': main()
