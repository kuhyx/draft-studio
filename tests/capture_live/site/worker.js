// worker.html's dedicated worker. `post`: POST, then a nested worker POSTs too; the page is told
// "Wątki zablokowane" only when both were refused. `nested`: the nested POST. `get`: a plain read.
const mode = location.search.slice(1);
const refused = (path) => fetch(path, { method: 'POST', body: '{}' }).then(() => false, () => true);

if (mode === 'nested') {
  refused('/api/nested').then((ok) => postMessage(ok));
} else if (mode === 'post') {
  refused('/api/worker').then((ok) => {
    const nested = new Worker('worker.js?nested');
    nested.onmessage = (event) => postMessage(ok && event.data ? 'Wątki zablokowane' : 'Wątek wysłał');
  });
} else {
  fetch('worker.html').then((res) => postMessage(res.ok ? 'Wątek odczytał' : 'Wątek bez odczytu'));
}
