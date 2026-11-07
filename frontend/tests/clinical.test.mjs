import { calculationTime } from '../src/ui/clinical.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {locateLesion,geometry,planeLabels,sortedTasks,taskId,metric,numberText,dateTime,studyCode,studyDate,studyHeading} from '../src/ui/clinical.ts';
const study={id:1,modality:'CT',slice_count:100,metadata_json:{volume_size_xyz:[64,64,100],volume_spacing_xyz:[.5,.5,2],volume_origin_xyz:[10,20,30],volume_direction:[1,0,0,0,1,0,0,0,1]}};
test('distinct physical centers locate different one-based image layers',()=>{assert.equal(locateLesion(study,{center_coord:[12,23,50]}).slice,11);assert.equal(locateLesion(study,{center_coord:[12,23,90]}).slice,31);});
test('direction and spacing are applied before slice numbering',()=>{const rotated={...study,metadata_json:{...study.metadata_json,volume_direction:[0,-1,0,1,0,0,0,0,-1]}};const p=locateLesion(rotated,{center_coord:[8,22,10]});assert.equal(p.slice,11);assert.ok(Math.abs(p.x-4.5/64*100)<1e-8);});
test('missing geometry, mismatched depth and out-of-volume centers never fall back to a sample layer',()=>{assert.equal(locateLesion({...study,metadata_json:{}},{center_coord:[12,23,50]}),null);assert.equal(geometry({...study,slice_count:101}),null);assert.equal(locateLesion(study,{center_coord:[12,23,9999]}),null);});
test('non-orthogonal direction is rejected and orientation labels use actual direction',()=>{assert.equal(geometry({...study,metadata_json:{...study.metadata_json,volume_direction:[1,.1,0,0,1,0,0,0,1]}}),null);assert.deepEqual(planeLabels(study),{left:'R',right:'L',top:'A',bottom:'P',plane:'轴位'});});
test('tasks are ordered by timestamps and stable identifiers rather than response position',()=>{const tasks=sortedTasks({tasks:[{id:8,status:'FAILED',created_at:'2026-11-06T01:00:00'},{id:11,status:'COMPLETED',created_at:'2026-11-06T02:00:00'},{id:2,status:'COMPLETED',created_at:'2026-11-05T02:00:00'}]});assert.equal(taskId(tasks[0]),11);});
test('missing metrics remain unavailable and valid zero/negative intensity is retained',()=>{assert.equal(metric({features:{}},'diameter'),null);assert.equal(numberText(metric({features:{}},'diameter')),'—');assert.equal(metric({features:{all_raw_features:{original_firstorder_Mean:0}}},'mean'),0);assert.equal(metric({features:{all_raw_features:{original_firstorder_Mean:-512}}},'mean'),-512);});
test('naive server UTC timestamps are displayed in the doctor timezone',()=>{assert.match(dateTime('2026-11-06T01:20:00'),/09:20/);});
test('record IDs are displayed unchanged and a missing study date stays unavailable',()=>{
 assert.equal(studyCode(1),'1');
 assert.equal(studyCode(268),'268');
 assert.equal(studyDate(null),'—');
 assert.equal(studyDate(undefined),'—');
 assert.equal(studyDate(''),'—');
 assert.equal(studyDate('2026-11-03T11:07:32'),'2026-11-03');
});

test('viewer heading combines imported patient identifier and unique study ID with a fallback',()=>{
 assert.equal(studyHeading({...study,patient:{patient_id:'LIDC-IDRI-0402'}}),'LIDC-IDRI-0402 · 检查 #1');
 assert.equal(studyHeading({...study,patient:{patient_id:'  LIDC-IDRI-0402  '}}),'LIDC-IDRI-0402 · 检查 #1');
 for(const patient of [undefined,null,{patient_id:''},{patient_id:'  '},{patient_id:'Unknown'}]) assert.equal(studyHeading({...study,patient}),'检查 #1');
});

test('calculation time converts saved UTC and ISO timestamps to UTC+08:00 with seconds',()=>{
 assert.equal(calculationTime('2026-11-06 03:05:09 UTC'),'2026/11/06 11:05:09');
 assert.equal(calculationTime('2026-11-06T20:05:09Z'),'2026/11/07 04:05:09');
 assert.equal(calculationTime('2026-11-06T11:05:09+08:00'),'2026/11/06 11:05:09');
 assert.equal(calculationTime('2026-11-06 03:05:09'),'2026/11/06 11:05:09');
 assert.equal(calculationTime(undefined),'—');
 assert.equal(calculationTime('invalid'),'—');
});
