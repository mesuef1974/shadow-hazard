'use strict';
const net = require('node:net');
net.createServer(client => {
  const upstream = net.connect('/proxy/egress.sock');
  upstream.on('error', () => client.destroy());
  client.on('error', () => upstream.destroy());
  client.pipe(upstream); upstream.pipe(client);
}).listen(8080, '127.0.0.1');
