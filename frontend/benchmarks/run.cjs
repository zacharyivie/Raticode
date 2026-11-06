const {spawnSync}=require('node:child_process');
const {backgroundLaunch}=require('../electron/tests/background-display.cjs');
const env={...process.env,ELECTRON_DISABLE_SANDBOX:'1',LIBGL_ALWAYS_SOFTWARE:'1'};
delete env.ELECTRON_RUN_AS_NODE;
// On Linux this runs on a hidden display, never the developer's desktop.
const launch=backgroundLaunch(require('electron'),['--no-sandbox',require('node:path').join(__dirname,process.env.PERF_AVATAR ? 'avatar.cjs' : 'editor.cjs')],env);
const result=spawnSync(launch.command,launch.args,{env:launch.env,stdio:'inherit',timeout:30000});
if(result.error) console.error(result.error);
process.exit(result.status ?? 1);
