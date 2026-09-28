const { chromium } = require('playwright'); const fs=require('fs');
(async()=>{ fs.rmSync(__dirname+'/f',{recursive:true,force:true}); fs.mkdirSync(__dirname+'/f');
 const b=await chromium.launch({executablePath:process.env.CHROME_PATH || undefined});
 const p=await b.newPage({viewport:{width:1280,height:720}});
 await p.goto('file://'+__dirname+'/player_built.html'); await p.waitForTimeout(300);
 const total=await p.evaluate(()=>window.TOTAL_MS); const only=process.argv[2];
 const times = only? only.split(',').map(Number) : Array.from({length:Math.ceil(total/1000*30)},(_,i)=>i*1000/30);
 for(let i=0;i<times.length;i++){ await p.evaluate(ms=>seek(ms),times[i]); await p.screenshot({path:`${__dirname}/f/${String(i).padStart(5,'0')}.png`}); }
 console.log('total',total,'frames',times.length); await b.close(); })();
