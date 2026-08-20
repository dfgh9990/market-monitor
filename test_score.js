// 复刻 index.html 中的纯评分函数，用真实数据验证映射
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
function scoreBreadth(b){
  const upR=b.up/b.total, stR=b.strong/b.total, wkR=b.weak/b.total;
  const score=clamp(upR*60+stR*40-wkR*30,0,100);
  return {score, details:{上涨家数:b.up,下跌家数:b.down,平盘:b.flat,上涨比例:(upR*100).toFixed(1)+'%',强势_3:b.strong,弱势_3:b.weak}};
}
function scoreVolume(b){
  const yi=b.turnover/1e8; let score=15,lvl='缩量';
  if(yi>12000){score=90;lvl='极度放量';}else if(yi>10000){score=80;lvl='放量';}
  else if(yi>8000){score=70;lvl='放量';}else if(yi>6000){score=55;lvl='温和';}
  else if(yi>4000){score=45;lvl='正常';}else if(yi>2500){score=30;lvl='缩量';}
  return {score, details:{总成交额:yi.toFixed(0)+'亿',量能等级:lvl}};
}
function scoreSector(secs){
  if(!secs.length) return {score:50,details:{板块状态:'—'}};
  const inflow=secs.filter(s=>s.main>0).length;
  const strong=secs.filter(s=>s.chg>2).length;
  const ratio=inflow/secs.length;
  const score=clamp(ratio*60+Math.min(strong,20)*2,0,100);
  const net=secs.reduce((a,s)=>a+s.main,0);
  return {score, details:{净流入板块:inflow,净流出板块:secs.length-inflow,强势板块_2:strong,主力净流向:(net/1e8).toFixed(1)+'亿'}};
}
function scoreFund(secs){
  if(!secs.length) return {score:50,details:{主力:'—',超大单:'—'}};
  const main=secs.reduce((a,s)=>a+s.main,0), sup=secs.reduce((a,s)=>a+s.sup,0);
  const my=main/1e8, sy=sup/1e8; let score;
  if(main>0&&sup>0) score=my>50?95:(my>20?90:85);
  else if(main>0&&sup<=0) score=55;
  else if(main<=0&&sup>0) score=45;
  else if(main<=0&&sup<=0) score=my<-50?10:(my<-20?20:35);
  else score=40;
  const dir=(main>0&&sup>0)?'双流入':((main<0&&sup<0)?'双流出':'分歧');
  return {score, details:{主力净流入:(my).toFixed(1)+'亿',超大单净流入:(sy).toFixed(1)+'亿',方向:dir}};
}
function scoreRsrs(idx){
  if(!idx.length) return {score:50,details:{沪深300:'—',上证:'—'}};
  const hs=idx.find(i=>i.name.includes('沪深300'))||{}, sh=idx.find(i=>i.name.includes('上证'))||{};
  const avg=((hs.chg||0)+(sh.chg||0))/2;
  return {score:clamp(50+avg*10,0,100), details:{'沪深300':(hs.chg||0).toFixed(2)+'%',上证:(sh.chg||0).toFixed(2)+'%',趋势:avg>0.3?'上行':(avg<-0.3?'下行':'横盘')}};
}
function scoreSentiment(b){
  const up=b.limitUp, dn=b.limitDown, tot=up+dn;
  const ratio=tot>0?up/tot:0.5;
  let score=ratio>0.8?100:(ratio>0.6?80:(ratio>0.5?65:(ratio>0.4?45:(ratio>0.2?25:10))));
  if(up>50) score=Math.min(100,score+10);
  if(dn>30) score=Math.max(0,score-10);
  return {score, details:{涨停:up,跌停:dn,涨跌停比:(ratio*100).toFixed(0)+'%',情绪:up>50?'极度乐观':(dn>30?'恐慌':'正常')}};
}
const WEIGHTS={breadth:.25,volume:.15,sector:.20,fund:.20,rsrs:.10,sentiment:.10};
function computePosition(total){
  if(total>=80) return {pos:90,text:'强势市场，积极进攻'};
  if(total>=60) return {pos:70,text:'偏强市场，正常操作'};
  if(total>=40) return {pos:50,text:'震荡市场，灵活应对'};
  if(total>=20) return {pos:30,text:'偏弱市场，防守为主'};
  return {pos:10,text:'弱势市场，空仓观望'};
}

// ===== 真实数据 2026-08-20 (来自 MCP) =====
const breadth={total:5547,up:449,down:5069,flat:29,strong:110,weak:1664,turnover:20794e8,limitUp:35,limitDown:6};
const idx=[{name:'上证指数',chg:0.24},{name:'沪深300',chg:0.09},{name:'深证成指',chg:0.59},{name:'创业板指',chg:0.64},{name:'科创50',chg:-0.87}];
// 板块：多数下跌，医药系领涨，整体主力净流出
const secs=Array.from({length:31},(_,i)=>({name:'S'+i,chg:i<3?4.4:(i<10?2.5:-1.2),main:i<3?1e9:(-1.5e9),sup:i<3?4e8:(-6e8)}));

const d=[scoreBreadth(breadth),scoreVolume(breadth),scoreSector(secs),scoreFund(secs),scoreRsrs(idx),scoreSentiment(breadth)];
const keys=['breadth','volume','sector','fund','rsrs','sentiment'];
const scores={}; keys.forEach((k,i)=>scores[k]=d[i].score);
const total=keys.reduce((a,k)=>a+scores[k]*WEIGHTS[k],0);
const pos=computePosition(total);
console.log('六维评分:');
keys.forEach((k,i)=>console.log('  '+k.padEnd(9), scores[k].toFixed(1), '|', JSON.stringify(d[i].details)));
console.log('\n综合评分:', total.toFixed(1));
console.log('仓位系数:', pos.pos+'%', '->', pos.text);

// 边界测试
console.log('\n边界映射:');
[85,65,45,25,5,80,60,40,20].forEach(t=>{
  const p=computePosition(t); console.log('  score',t,'=> pos',p.pos+'%',p.text);
});
