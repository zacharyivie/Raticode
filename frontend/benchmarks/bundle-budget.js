// Rolldown (Vite 8) moves Monaco's shared editor code out of the `monaco` chunk into a chunk
// that `monaco` imports, so the Monaco budget also counts imported chunks of monaco-editor code.
function monacoBytes(chunk,bundle){
  let bytes=Buffer.byteLength(chunk.code);
  for(const fileName of chunk.imports) {
    const imported=bundle[fileName];
    if(imported?.type==='chunk'&&imported.moduleIds.some(id=>id.includes('/node_modules/monaco-editor/'))) bytes+=Buffer.byteLength(imported.code);
  }
  return bytes;
}

export function bundleBudget() {
  return {name:'bundle-budget',generateBundle(_options,bundle){
    for(const chunk of Object.values(bundle)) {
      if(chunk.type!=='chunk') continue;
      // Only the entry and Monaco have budgets; other lazy chunks are unlimited.
      const limit=chunk.isEntry?1000000:chunk.name==='monaco'?3050000:null;
      if(limit===null) continue;
      const bytes=chunk.isEntry?Buffer.byteLength(chunk.code):monacoBytes(chunk,bundle);
      if(bytes>limit) this.error(`${chunk.fileName}: ${bytes} bytes exceeds ${limit}-byte ${chunk.isEntry?'entry':'Monaco'} budget`);
    }
  }};
}
