const http = require('node:http');
const [name, rawPort] = process.argv.slice(2);
const server = http.createServer((req, res) => {
  console.log(`${name}: ${req.method} ${req.url}`);
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ service: name, ok: true }));
});
server.listen(Number(rawPort), '127.0.0.1', () => console.log(`${name} ready at http://127.0.0.1:${rawPort}`));
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
