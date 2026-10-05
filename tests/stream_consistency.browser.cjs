// Real production renderers; isolated fixture only, no provider/host traffic.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const frames=page=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));

// A grouped run of shell calls opens and folds back. Terminal and Compact keep
// their header as the control (mouse and keyboard). Console prints the opened
// calls in place of the summary line, as Claude Code does, and a click on the
// printed calls folds them back.
async function checkGroup(page,scope,view){
  const group=page.locator(`${scope} .is-stack, ${scope} .is-console-group`).first();
  const toggle=group.locator(':scope > .chat-step-main > .chat-step-head [data-step-toggle]').first();
  if(await toggle.getAttribute('aria-expanded')==='true'){
    if(view==='console')await group.locator(':scope > .chat-step-main > .chat-console-bundle .chat-step-node').first().click({force:true});
    else await toggle.click();
  }
  assert.equal(await toggle.getAttribute('aria-expanded'),'false',`${view}: ${scope} group starts folded`);
  await toggle.click();
  assert.equal(await toggle.getAttribute('aria-expanded'),'true',`${view}: ${scope} group opens`);
  if(view==='console'){
    assert.equal(await group.locator(':scope > .chat-step-main > .chat-console-bundle').isVisible(),true,`${view}: opened calls are printed`);
    assert.equal(await toggle.isVisible(),false,`${view}: the summary line gives way to its calls`);
    await group.locator(':scope > .chat-step-main > .chat-console-bundle .chat-step-node').first().click({force:true});
    assert.equal(await toggle.getAttribute('aria-expanded'),'false',`${view}: a click on the printed calls folds them back`);
    assert.equal(await toggle.isVisible(),true,`${view}: the summary line returns`);
    await toggle.focus();await page.keyboard.press('Enter');
    assert.equal(await toggle.getAttribute('aria-expanded'),'true',`${view}: Enter opens the summary line`);
    await group.locator(':scope > .chat-step-main > .chat-console-bundle .chat-step-node').first().click({force:true});
  } else {
    assert.equal(await toggle.isVisible(),true,`${view}: collapse control remains visible`);
    await toggle.click();
    assert.equal(await toggle.getAttribute('aria-expanded'),'false');
    await toggle.focus();await page.keyboard.press('Enter');
    assert.equal(await toggle.getAttribute('aria-expanded'),'true');
    await page.keyboard.press('Space');
  }
  assert.equal(await toggle.getAttribute('aria-expanded'),'false',`${view}: ${scope} group folds`);
}

(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {})});
  try {
    const page = await browser.newPage({reducedMotion:'reduce'});
    const errors=[];
    page.on('pageerror', error=>errors.push(error.message));
    await page.route('**/*', route=>new URL(route.request().url()).origin===origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(()=>window.fuseToolFixture);
    for (const width of [1280,390]) for (const view of ['cli','compact','console']) {
      await page.setViewportSize({width,height:900});
      await page.evaluate(view=>{
        const f=fuseToolFixture;f.view(view);
        f.mount([{id:'thinking-'+view,reasoning:'First thought',tools:[],segments:[{type:'reasoning',text:'First thought'}]}],true);
      },view);
      if (view==='console') {
        // Console prints a thought only once it lands (Claude Code style): the
        // live dock carries it while it streams, then a "Thought for" line.
        assert.equal(await page.locator('.chat-log :is([data-reason-preview], .chat-console-thought)').count(),0,`${view}: an unfinished thought prints no row`);
        await page.evaluate(()=>fuseToolFixture.stream(0,{reasoning:'First thought and next thought',segments:[{type:'reasoning',text:'First thought and next thought'}]}));
        assert.equal(await page.locator('.chat-log .chat-console-thought').count(),0,`${view}: streamed thinking text alone repaints nothing`);
        await page.evaluate(()=>fuseToolFixture.mount([{id:'thinking-console',reasoning:'First thought and next thought',tools:[],segments:[{type:'reasoning',text:'First thought and next thought'}]}],false));
        const line=page.locator('.chat-log .is-console-group').first();
        assert.match(await line.locator(':scope > .chat-step-main > .chat-step-head .chat-step-label').textContent(),/^Thought for /,`${view}: landed thought reads as a summary line`);
        await line.locator('[data-step-toggle]').first().click();
        const thought=page.locator('.chat-log .chat-console-thought');
        assert.equal(await thought.isVisible(),true,`${view}: expanding the line shows the thought`);
        assert.match(await thought.textContent(),/next thought/);
      } else {
        assert.equal(await page.locator('.chat-log [data-reason-preview]').count(),1,`${view}: live reasoning`);
        assert.equal(await page.locator('.chat-log [data-reason-preview]').isVisible(),true,`${view}: reasoning text is visible`);
        await page.evaluate(()=>fuseToolFixture.stream(0,{reasoning:'First thought and next thought',segments:[{type:'reasoning',text:'First thought and next thought'}]}));
        assert.match(await page.locator('.chat-log [data-reason-preview]').textContent(),/next thought/);
      }
      await page.evaluate(({view,width})=>fuseToolFixture.mount([{id:`group-${view}-${width}`,tools:Array.from({length:5},(_,i)=>({id:'shell'+i,name:'run_terminal_command',status:'done',state:'completed',command:'printf audit',output:'sample\n'.repeat(10),exit_code:0}))}],false),{view,width});
      await checkGroup(page,'.chat-log',view);
      const detail=await page.evaluate(view=>{
        const f=fuseToolFixture;
        f.mount([{id:'output-'+view,tools:[{id:'detail',name:'run_terminal_command',status:'done',state:'completed',command:'printf audit',output:'OLD_OUTPUT',exit_code:0}]}],false);
        f.update('detail',{output:'NEW_OUTPUT'},false);
        // Collapsed rows build their details on first open; open it so the
        // check covers what a reader sees, not a hidden stale copy.
        const lazy=document.querySelector('.chat-log [data-lazy-detail]');
        if(lazy)lazy.closest('[data-step]').querySelector(':scope > .chat-step-main > .chat-step-head [data-step-toggle]').click();
        return document.querySelector('.chat-log').textContent;
      },view);
      assert.ok(detail.includes('NEW_OUTPUT') && !detail.includes('OLD_OUTPUT'),`${view}: details refresh`);
    }
    const count=await page.evaluate(()=>{
      const f=fuseToolFixture;const tools=Array.from({length:5},(_,i)=>({id:'t'+i,name:'read_file',status:'running'}));
      f.mount([{id:'idempotent',content:'hello',tools,segments:[{type:'content',text:'hello'}]}],true);
      const patch={event:'message.patch',content:{from:5,text:''},reasoning:{from:0,text:''},segments:{base:1,append:tools.map(t=>({type:'tool',id:t.id}))}};
      f.progress(0,{events:[{fuse:patch}]});f.progress(0,{events:[{fuse:patch}]});
      return f.snapshot()[0].segments.filter(s=>s.type==='tool').length;
    });
    assert.equal(count,5,'repeated segment append is idempotent');

    let run={id:'audit-child',event_cursor:1,message_id:'child-bubble',thread_id:'fixture-thread',parent_run_id:'parent',status:'running',message:{content:'',reasoning:'',tools:[],segments:[]},messages:[],description:'Fixture',created_at:new Date().toISOString(),updated_at:new Date().toISOString()};
    let hold=false,release;
    await page.route('**/chat/runs/audit-child',async route=>{
      const captured=structuredClone(run);
      if(hold){hold=false;await new Promise(resolve=>release=resolve);}
      await route.fulfill({json:captured});
    });
    await page.route('**/chat/threads/fixture-thread/agents',route=>route.fulfill({json:{agents:[]}}));
    await page.route('**/chat/threads/fixture-thread/jobs',route=>route.fulfill({json:{jobs:[]}}));
    await page.evaluate(()=>{fuseToolFixture.view('cli');fuseToolFixture.mount([{id:'parent',tools:[{id:'spawn',name:'spawn_subagent',status:'running',background:true,child_id:'audit-child'}]}],true,'fixture-thread');});
    await page.locator('[data-open-agent="audit-child"]').first().click();
    await page.waitForSelector('[data-agent-current]',{state:'attached'});
    const tools=Array.from({length:5},(_,i)=>({id:'t'+i,name:'read_file',status:'running',path:'file'+i}));
    await page.evaluate(tools=>fuseToolFixture.event({id:10,type:'agent.progress',thread_id:'fixture-thread',payload:{run_id:'audit-child',events:tools.map(t=>({fuse:{event:'tool',...t}}))}}),tools);
    // The agent pane repaints once per animation frame; read it after a frame.
    await frames(page);
    assert.equal(await page.locator('[data-agent-current] [data-tool]').count(),5,'live child progress renders every tool');
    run.event_cursor=10;run.message.tools=tools;run.message.segments=tools.map(t=>({type:'tool',id:t.id}));
    await page.evaluate(()=>fuseToolFixture.pollAgent());
    assert.equal(await page.locator('[data-agent-log-body] [data-tool]').count(),5,'poll cannot duplicate live tools');

    hold=true;await page.evaluate(()=>{window.heldPoll=fuseToolFixture.pollAgent()});
    for(let i=0;i<100&&!release;i++)await new Promise(r=>setTimeout(r,10));
    assert.ok(release);
    run.status='completed';run.event_cursor=11;
    await page.evaluate(()=>fuseToolFixture.event({id:11,type:'agent.completed',thread_id:'fixture-thread',payload:{run_id:'audit-child',child_id:'audit-child',status:'completed'}}));
    release();await page.evaluate(()=>window.heldPoll);
    assert.equal(await page.locator('[data-agent-status]').textContent(),'completed','stale poll cannot undo completion');
    await page.waitForTimeout(100);
    for(const view of ['cli','compact','console']){
      run.event_cursor++;
      run.status='running';run.messages=[];run.message={content:'',reasoning:'Child thinking',tools:[],segments:[{type:'reasoning',text:'Child thinking'}]};
      await page.evaluate(view=>fuseToolFixture.view(view),view);await page.evaluate(()=>fuseToolFixture.pollAgent());
      await frames(page);
      // Console prints a child's thought once it lands, like the main log.
      assert.equal(await page.locator('[data-agent-current] :is([data-reason-preview], .chat-console-thought)').count(),view==='console'?0:1,`${view}: child thinking`);
      run.event_cursor++;
      run.message={content:'',reasoning:'',tools:Array.from({length:5},(_,i)=>({id:'s'+i,name:'run_terminal_command',status:'done',state:'completed',command:'pwd',output:'fixture\n'.repeat(8),exit_code:0}))};
      await page.evaluate(()=>fuseToolFixture.pollAgent());
      await checkGroup(page,'[data-agent-current]',view);
    }
    run.event_cursor++;run.messages=[{id:'audit-child:agent-hist-1',role:'assistant',content:'',tools:[{id:'historical',name:'read_file',status:'interrupted',path:'file'}]}];run.message={content:'',tools:[]};
    await page.evaluate(()=>{fuseToolFixture.view('cli');return fuseToolFixture.pollAgent()});
    run.event_cursor++;run.messages[0].tools[0].status='done';
    await page.evaluate(()=>fuseToolFixture.pollAgent());
    assert.equal(await page.locator('[data-agent-log-body] [data-tool="read_file"]').getAttribute('data-state'),'done','historical status refreshes');
    run.event_cursor++;run.messages=[];run.message={content:'',tools:[{id:'nested',name:'spawn_subagent',status:'running',background:true,child_id:'grandchild'}]};
    await page.evaluate(()=>fuseToolFixture.pollAgent());
    await page.evaluate(id=>fuseToolFixture.event({id,type:'agent.completed',thread_id:'fixture-thread',payload:{run_id:'grandchild',child_id:'grandchild',status:'completed',message:{content:'Report'}}}),run.event_cursor+1);
    await frames(page);
    assert.equal(await page.locator('[data-agent-current] [data-tool="spawn_subagent"]').getAttribute('data-state'),'done','nested completion refreshes');
    assert.deepEqual(errors,[]);
    console.log('PASS: three views, desktop/mobile, thinking, disclosures, detail updates, child snapshots, append replay and nested status');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
