export function bundleBudget() {
  return {name:'bundle-budget',generateBundle(_options,bundle){
    for(const chunk of Object.values(bundle)) {
      if(chunk.type!=='chunk') continue;
      // Only the entry and Monaco have budgets; other lazy chunks are unlimited.
      const limit=chunk.isEntry?1000000:chunk.name==='monaco'?3050000:null;
      if(limit===null) continue;
      const bytes=Buffer.byteLength(chunk.code);
      if(bytes>limit) this.error(`${chunk.fileName}: ${bytes} bytes exceeds ${limit}-byte ${chunk.isEntry?'entry':'Monaco'} budget`);
    }
  }};
}
