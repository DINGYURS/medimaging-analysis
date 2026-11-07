import test from 'node:test';
import assert from 'node:assert/strict';
import {positiveId,readWorkspaceView} from '../src/ui/workspaceNavigation.ts';
const view={studyId:101,taskId:201,slice:73,windowWidth:350,windowCenter:40,preset:'纵隔窗',zoom:1.2,selectedId:302,activeTab:'features',sort:'slice',collapsed:false,showMask:false,maskOpacity:70};
test('return preserves the exact viewer settings for the same study and historical task',()=>{
 assert.deepEqual(readWorkspaceView(view,101,201,100,[301,302]),view);
});
test('viewer state cannot cross studies, tasks, or out-of-range slices',()=>{
 assert.equal(readWorkspaceView(view,102,201,100,[302]),null);
 assert.equal(readWorkspaceView(view,101,202,100,[302]),null);
 assert.equal(readWorkspaceView(view,101,201,50,[302]),null);
 assert.equal(readWorkspaceView({...view,zoom:Infinity},101,201,100,[302]),null);
 assert.equal(readWorkspaceView(null,101,201,100,[302]),null);
});
test('a removed lesion does not restore an invalid selection',()=>{
 assert.equal(readWorkspaceView(view,101,201,100,[301]).selectedId,null);
});
test('route IDs reject missing, repeated, negative and nonintegral values',()=>{
 assert.equal(positiveId('201'),201);
 for(const value of [undefined,null,'',0,-1,'1.5','nope',['201'],Infinity]) assert.equal(positiveId(value),null);
});
