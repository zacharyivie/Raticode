const {spawnSync}=require('node:child_process');
const env={...process.env,ELECTRON_DISABLE_SANDBOX:'1',LIBGL_ALWAYS_SOFTWARE:'1'};
delete env.ELECTRON_RUN_AS_NODE;
const result=spawnSync(require('electron'),['--no-sandbox',require('node:path').join(__dirname,process.env.PERF_AVATAR ? 'avatar.cjs' : 'editor.cjs')],{env,stdio:'inherit',timeout:30000});
if(result.error) console.error(result.error);
process.exit(result.status ?? 1);
