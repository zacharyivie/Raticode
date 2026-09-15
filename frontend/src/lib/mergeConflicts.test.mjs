import assert from "node:assert/strict";
import test from "node:test";
import { installConflictControls } from "./mergeConflicts.js";

test("ordinary edits skip full-document reads while newly typed conflicts still get controls", () => {
  let text = "plain text";
  let reads = 0, listener, provider;
  const model = {
    getValue: () => { reads++; return text; },
    getVersionId: () => 1,
    getLineCount: () => text.split("\n").length,
    getLineContent: line => text.split("\n")[line - 1],
    findMatches: () => /^<{7}(?: |$)/m.test(text) ? [{}] : [],
    onDidChangeContent: callback => { listener = callback; return { dispose() {} }; },
  };
  const monaco = {
    languages: { registerCodeLensProvider: (_language, value) => { provider = value; return { dispose() {} }; } },
    Range: class Range {},
  };
  const editor = { addCommand: () => "command", deltaDecorations: () => [] };
  const controls = installConflictControls(monaco, editor, model);
  const edit = (value, line = 1) => {
    text = value;
    listener({ changes: [{ range: { startLineNumber: line }, text: value }] });
    return provider.provideCodeLenses(model).lenses;
  };
  for (let i = 0; i < 100; i++) assert.equal(edit(`plain text ${i}`).length, 0);
  assert.equal(reads, 0);
  // Incomplete markers must remain watched as the rest of a conflict is typed.
  edit("<<<<<<< current\nours\n=======\ntheirs\n");
  assert.equal(edit("<<<<<<< current\nours\n=======\ntheirs\n>>>>>>> incoming", 5).length, 3);
  assert.equal(edit("resolved").length, 0);
  const afterResolution = reads;
  edit("resolved text");
  assert.equal(reads, afterResolution);
  controls.dispose();
});

test('ordinary typing avoids whole-document scans; introduced conflicts and resolution update lenses',()=>{
 let text='ordinary text\n',version=1,reads=0,listener,provider,decorations=[];
 const model={getLineCount:()=>text.split("\n").length,findMatches:()=>/^<{7}(?: |$)/m.test(text)?[{}]:[],getValue(){reads++;return text;},getVersionId:()=>version,getLineContent:line=>text.split('\n')[line-1],onDidChangeContent(fn){listener=fn;return {dispose(){}};}};
 const editor={addCommand:()=> 'accept',deltaDecorations(_old,next){decorations=next;return [];}};
 const monaco={Range:class {constructor(start){this.startLineNumber=start;}},languages:{registerCodeLensProvider(_selector,next){provider=next;return {dispose(){}};}}};
 const controls=installConflictControls(monaco,editor,model);
 reads=0;
 for(let i=0;i<80;i++){text='x'+text;version++;listener({changes:[{range:{startLineNumber:1},text:'x'}]});provider.provideCodeLenses(model);}
 assert.equal(reads,0);
 text='<<<<<<< current\nours\n=======\ntheirs\n>>>>>>> incoming\n';version++;
 listener({changes:[{range:{startLineNumber:1},text}]});
 assert.equal(provider.provideCodeLenses(model).lenses.length,3);
 assert.equal(decorations.length,2);
 text='ours\n';version++;listener({changes:[{range:{startLineNumber:1},text}]});
 assert.equal(provider.provideCodeLenses(model).lenses.length,0);
 assert.equal(decorations.length,0);
 controls.dispose();
});
