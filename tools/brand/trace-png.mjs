// Dependency-free color contour reconstruction. Original PNG is never modified.
// Usage: node tools/brand/trace-png.mjs input.png output.svg [logo]
import fs from 'node:fs';
import zlib from 'node:zlib';
const [input, output, mode] = process.argv.slice(2);
if (!input || !output) throw Error('Input PNG and output SVG required');
const b = fs.readFileSync(input);
if (b.subarray(0,8).toString('hex') !== '89504e470d0a1a0a') throw Error('Not PNG');
const w=b.readUInt32BE(16), h=b.readUInt32BE(20), channels=b[25]===6?4:3;
if(b[24]!==8 || ![2,6].includes(b[25]) || b[28]!==0) throw Error('Only non-interlaced RGB/RGBA8 PNG');
let chunks=[];
for(let p=8;p<b.length;){const n=b.readUInt32BE(p);if(b.toString('ascii',p+4,p+8)==='IDAT')chunks.push(b.subarray(p+8,p+8+n));p+=n+12;}
const raw=zlib.inflateSync(Buffer.concat(chunks)), stride=w*channels, pixels=Buffer.alloc(stride*h);
const paeth=(a,b,c)=>{let p=a+b-c,pa=Math.abs(p-a),pb=Math.abs(p-b),pc=Math.abs(p-c);return pa<=pb&&pa<=pc?a:pb<=pc?b:c;};
for(let y=0;y<h;y++){
 const f=raw[y*(stride+1)];if(f>4)throw Error('Invalid PNG filter');
 for(let x=0;x<stride;x++){let a=x>=channels?pixels[y*stride+x-channels]:0,c=y&&x>=channels?pixels[(y-1)*stride+x-channels]:0,t=y?pixels[(y-1)*stride+x]:0;
 pixels[y*stride+x]=(raw[y*(stride+1)+x+1]+[0,a,t,Math.floor((a+t)/2),paeth(a,t,c)][f])&255;}
}
const hist=new Map();let transparent=0,opaque=0;
for(let p=0;p<w*h;p++){const i=p*channels,a=channels===4?pixels[i+3]:255;if(a<128){transparent++;continue;}opaque++;const key=(pixels[i]>>3)<<10|(pixels[i+1]>>3)<<5|(pixels[i+2]>>3);hist.set(key,(hist.get(key)||0)+1);}
if(!transparent||!opaque)throw Error('Meaningful transparency required');
const samples=[...hist].map(([key,n])=>({c:[((key>>10)&31)*8+4,((key>>5)&31)*8+4,(key&31)*8+4],n,key}));
const dist=(a,b)=>(a[0]-b[0])**2+(a[1]-b[1])**2+(a[2]-b[2])**2;
let palette;
if(mode==='logo') palette=[[20,45,78],[0,127,130],[244,181,68]];
else {
 palette=[samples.reduce((a,b)=>a.n>b.n?a:b).c];
 for(let k=1;k<64;k++){let best=samples.reduce((a,s)=>{const score=Math.min(...palette.map(c=>dist(c,s.c)))*Math.sqrt(s.n);return score>a.score?{score,c:s.c}:a;},{score:-1});palette.push(best.c);}
 for(let iteration=0;iteration<10;iteration++){
  const sums=palette.map(()=>[0,0,0,0]);
  for(const s of samples){let j=nearest(s.c,palette);for(let c=0;c<3;c++)sums[j][c]+=s.c[c]*s.n;sums[j][3]+=s.n;}
  palette=palette.map((c,j)=>sums[j][3]?sums[j].slice(0,3).map(v=>Math.round(v/sums[j][3])):c);
 }
}
function nearest(c,p){let n=0,d=Infinity;for(let j=0;j<p.length;j++){let t=dist(c,p[j]);if(t<d){d=t;n=j;}}return n;}
const lookup=new Map(samples.map(s=>[s.key,nearest(s.c,palette)])), labels=new Int16Array(w*h).fill(-1);
for(let p=0;p<w*h;p++){let i=p*channels;if(channels===4&&pixels[i+3]<128)continue;const key=(pixels[i]>>3)<<10|(pixels[i+1]>>3)<<5|(pixels[i+2]>>3);labels[p]=lookup.get(key);}
// Remove disconnected alpha specks without deleting interior color details.
const seen=new Uint8Array(w*h);let dropped=0;
for(let p=0;p<labels.length;p++){if(seen[p]||labels[p]<0)continue;let component=[p];seen[p]=1;
 for(let q=0;q<component.length;q++){let v=component[q],x=v%w;for(const n of [x?v-1:-1,x<w-1?v+1:-1,v-w,v+w])if(n>=0&&n<labels.length&&!seen[n]&&labels[n]>=0){seen[n]=1;component.push(n);}}
 if(component.length<16){for(const n of component)labels[n]=-1;dropped+=component.length;}
}
const width=w+1,edges=palette.map(()=>new Map());
function edge(k,a,b){let list=edges[k].get(a);if(list)list.push(b);else edges[k].set(a,[b]);}
for(let y=0;y<h;y++)for(let x=0;x<w;x++){let p=y*w+x,k=labels[p];if(k<0)continue;let a=y*width+x;
 if(!y||labels[p-w]!==k)edge(k,a,a+1);
 if(x===w-1||labels[p+1]!==k)edge(k,a+1,a+1+width);
 if(y===h-1||labels[p+w]!==k)edge(k,a+1+width,a+width);
 if(!x||labels[p-1]!==k)edge(k,a+width,a);
}
function simplify(points,tolerance=.65){
 if(points.length<=2)return points;let a=points[0],b=points.at(-1),dx=b[0]-a[0],dy=b[1]-a[1],len=dx*dx+dy*dy,max=0,idx=0;
 for(let i=1;i<points.length-1;i++){let p=points[i],t=len?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/len)):0,d=(p[0]-a[0]-t*dx)**2+(p[1]-a[1]-t*dy)**2;if(d>max){max=d;idx=i;}}
 return max>tolerance*tolerance?[...simplify(points.slice(0,idx+1),tolerance).slice(0,-1),...simplify(points.slice(idx),tolerance)]:[a,b];
}
let paths=[],contours=0;
for(let k=0;k<edges.length;k++){let map=edges[k],segments=[];
 while(map.size){let start=map.keys().next().value,current=start,loop=[];
  do{loop.push([current%width,Math.floor(current/width)]);let list=map.get(current);if(!list)throw Error('Open contour');let next=list.pop();if(!list.length)map.delete(current);current=next;}while(current!==start);
  if(loop.length<4)continue;
  // Split closed loops before RDP, avoiding coincident endpoints.
  let mid=Math.floor(loop.length/2),points=[...simplify(loop.slice(0,mid+1)).slice(0,-1),...simplify([...loop.slice(mid),loop[0]]).slice(0,-1)];
  if(points.length>=3){segments.push('M'+points.map(p=>p.join(',')).join('L')+'Z');contours++;}
 }
 const color='#'+palette[k].map(v=>v.toString(16).padStart(2,'0')).join('');
 if(segments.length)paths.push(`<path fill="${color}" fill-rule="evenodd" d="${segments.join('')}"/>`);
}
fs.writeFileSync(output,`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" role="img"><title>${mode==='logo'?'ظل الخطر':'راصد'}</title><desc>Automatic color contour reconstruction; transparent background; no embedded raster. Source PNG remains authoritative for continuous shading.</desc>${paths.join('\n')}</svg>\n`);
console.log(JSON.stringify({input,output,width:w,height:h,transparent,opaque,palette:palette.length,contours,droppedAlphaSpecks:dropped,bytes:fs.statSync(output).size}));
