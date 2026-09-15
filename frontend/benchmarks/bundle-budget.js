export function bundleBudget() {
  return {name:'bundle-budget',generateBundle(_options,bundle){
    for(const chunk of Object.values(bundle)) {
      if(chunk.type!=='chunk') continue;
      const bytes=Buffer.byteLength(chunk.code);
      const limit=chunk.isEntry?1000000:chunk.name==='monaco'?3050000:400000;
      if(bytes>limit) this.error(`${chunk.fileName}: ${bytes} bytes exceeds ${limit}-byte ${chunk.isEntry?'entry':'lazy chunk'} budget`);
    }
  }};
}
