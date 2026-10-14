/* WDTT chunked bootstrap: тянет app.js кусками по ~14 КБ (Range) и собирает.

Контекст: режущие провайдеры обрывают соединение после ~16 КБ, и файл
больше лимита обычным <script src> не загрузится никогда. Каждый кусок
сервер отдаёт со своим соединением (Connection: close), так что лимит
начинается заново. Сам boot.js < 1 КБ и влезает целиком.
*/
(() => {
  "use strict";
  const meta = (name) => document.querySelector(`meta[name="${name}"]`).content;
  const url = meta("app-js-url"), total = Number(meta("app-js-size") || 0);
  const SIZE = 14000, parts = [];
  let offset = 0, attempts = 0;
  const classic = () => {
    const s = document.createElement("script");
    s.src = url;
    document.body.appendChild(s);
  };
  const next = () => {
    if (offset >= total && total > 0) {
      const s = document.createElement("script");
      s.src = URL.createObjectURL(new Blob(parts, { type: "text/javascript" }));
      document.body.appendChild(s);
      return;
    }
    fetch(url, { headers: { Range: `bytes=${offset}-${Math.min(offset + SIZE - 1, total - 1)}` } })
      .then((r) => {
        if (r.status !== 206 && r.status !== 200) throw new Error(`HTTP ${r.status}`);
        return r.arrayBuffer().then((b) => [r.status, b]);
      })
      .then(([status, buf]) => {
        if (status === 200) {
          parts.length = 0;
          parts.push(buf);
          offset = total || buf.byteLength;
        } else {
          parts.push(buf);
          offset += buf.byteLength;
        }
        attempts = 0;
        next();
      })
      .catch(() => {
        if (++attempts > 3) classic();
        else next();
      });
  };
  if (!url || !(total > 0)) classic();
  else next();
})();
