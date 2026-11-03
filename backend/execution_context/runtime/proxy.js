'use strict';
const http = require('node:http');
const net = require('node:net');
const dns = require('node:dns').promises;
const fs = require('node:fs');
const allowed = new Set(['chatgpt.com', 'api.openai.com', 'auth.openai.com']);
const socket = '/proxy/egress.sock';
// Only globally routable IPv4 destinations; reject local, reserved and metadata addresses.
function publicAddress(address) {
  if (net.isIP(address) !== 4) return false;
  const [a,b] = address.split('.').map(Number);
  return !(a===0 || a===10 || a===127 || a>=224 || (a===100 && b>=64 && b<=127)
    || (a===169 && b===254) || (a===172 && b>=16 && b<=31) || (a===192 && [0,168].includes(b))
    || (a===198 && [18,19,51].includes(b)) || (a===203 && b===0));
}
const server = http.createServer((req,res) => { res.writeHead(403); res.end(); });
server.on('connect', async (req,client,head) => {
  try {
    const url = new URL('https://' + req.url);
    if (url.port && url.port !== '443') throw Error('port denied');
    if (!allowed.has(url.hostname) || url.username || url.password || url.pathname !== '/') throw Error('host denied');
    const answers = await dns.lookup(url.hostname, {all:true,family:4});
    if (!answers.length || !answers.every(row => publicAddress(row.address))) throw Error('address denied');
    const upstream = net.connect({host:answers[0].address,port:443,timeout:15000});
    upstream.on('connect', () => {
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head.length) upstream.write(head);
      client.pipe(upstream); upstream.pipe(client);
    });
    upstream.on('error', () => client.destroy());
    upstream.on('timeout', () => upstream.destroy());
    client.on('error', () => upstream.destroy());
    client.on('close', () => upstream.destroy());
  } catch { client.end('HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n'); }
});
server.listen(socket, () => fs.chmodSync(socket,0o666));
