import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const page=readFileSync(new URL('../assets/ui-review-board/index.html',import.meta.url),'utf8');
const saving=page.slice(page.indexOf('let screenNoteWrites='),page.indexOf('function render()'));
test('queued notes retain a preceding status and do not redraw a different screen', async()=>{
  const field={value:'First note'}, status={}, writes=[];
  const state={id:'one',evidenceId:'exact-evidence',group:'Editing',required:true};
  const capture={id:'capture',screens:[state],coverageRows:[{group:'Editing'}]};
  const item={...state,capture};
  let redraws=0, release;
  const first=new Promise(resolve=>{release=resolve;});
  const context={SCREENS:{chromium:[capture]},view:{kind:'screens',profile:'chromium'},cursor:0,
    items:()=>[item],$:selector=>selector==='#screen-note'?field:status,
    list:()=>{redraws++;},detail:()=>{redraws++;},
    api:async(_,options)=>{const body=JSON.parse(options.body);writes.push(body);if(writes.length===1)await first;return {status:body.status,note:body.note};}};
  runInNewContext(saving,context);
  const bookmark=context.saveScreenNote(item,{status:'bookmark'});
  field.value='A later note';
  const note=context.saveScreenNote(item,{});
  await Promise.resolve();
  assert.equal(writes.length,1,'writes are serialized');
  context.view.profile='webkit-touch';
  release();await Promise.all([bookmark,note]);
  assert.deepEqual(writes.map(w=>[w.status,w.note]),[['bookmark','First note'],['bookmark','A later note']]);
  assert.equal(state.human.note,'A later note');
  assert.equal(state.human.status,'bookmark');
  assert.equal(redraws,0,'a completed save must not redraw a different profile');
});

test('a completed save does not replace newer unsaved text',async()=>{
  const field={value:'Saved text'},status={},state={id:'one',evidenceId:'same',group:'Editing',required:true};
  const capture={id:'capture',screens:[state],coverageRows:[]},item={...state,capture};
  let release,detailCalls=0;
  const response=new Promise(resolve=>{release=resolve;});
  const context={SCREENS:{chromium:[capture]},view:{kind:'screens',profile:'chromium'},cursor:0,items:()=>[item],
    $:selector=>selector==='#screen-note'?field:status,list:()=>{},detail:()=>{detailCalls++;},api:()=>response};
  runInNewContext(saving,context);
  const pending=context.saveScreenNote(item,{});
  field.value='Still typing';release({status:null,note:'Saved text'});await pending;
  assert.equal(detailCalls,0);assert.equal(field.value,'Still typing');assert.match(status.textContent,/not yet saved/);
});
