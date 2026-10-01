'use strict';
// A pagina inicial agora e modulo ES (lista/app.js, carregado pela propria
// pagina). Este arquivo so atende HTML antigo guardado em cache, que ainda
// pede /app.js: carrega o modulo novo, uma vez.
if (!document.querySelector('script[type="module"][src="/lista/app.js"]')) {
  import('/lista/app.js');
}
